# pylint: disable=no-name-in-module,c-extension-no-member
# Pylint does not see mypy modules for writing extensions.
'''
Mypy plugin describing ``fastenum`` classes' interface:

  - attribute access
  - iterating over class definition
  - ``__getitem__`` access


Sources:
  - https://mypy.readthedocs.io/en/latest/extending_mypy.html
  - https://medium.com/@erick.peirson/type-checking-with-json-schema-in-python-3244f3917329

Source code inspiration (many things were taken from existing plugins):
  - https://github.com/python/mypy/tree/master/mypy/plugins (all)
  - https://github.com/python/mypy/blob/master/mypy/plugins/dataclasses.py
  - https://github.com/python/mypy/blob/master/mypy/plugins/attrs.py


We added explanations where necessary. Please go through the module
and read it in the following order:

  #. :code:`def plugin(_: str) -> Type[FastEnumPlugin]:`
      - Defines the plugin itself - this is what will be registered as a plugin.
      - You can return different implementations based on the mypy version (which is the only argument).
  #. :code:`class FastEnumPlugin(mypy.plugin.Plugin):`
      - The mypy plugin, which is just a hook-registering class
      - See https://mypy.readthedocs.io/en/latest/extending_mypy.html#high-level-overview
      - We are using two main hooks:
        - :code:`get_type_analyze_hook` to change the type definition mypy sees;
        - :code:`get_base_class_hook` to change the class definition inherited from the :code:`Enum` class.
  #. As the first hook handler please read :code:`transform_enum_class_def` which intercepts
     the class definition. As part of this reading go through :code:`_define_method`
      - This should be the simpler part and more understandable.
  #. After understanding the previous part go to :code:`transform_enum_type`
      - This is a bit magical, as we redefine :code:`Enum` and :code:`EnumMeta` for mypy.

The last thing is how this plugin is registered which is a bit sad in mypy
because the only thing you can do is intercept fullnames (module, classname, method, ...)
of any AST object and tell mypy whether you want to do something with that object.

But once you return your hook handler - there is no way back.
So for example Enum-inherited classes can be seen from mypy as these fullnames:

  - :code:`controllers.orders.types.fastenum`
  - :code:`main.Enum`

So this way we will collide with the original Enum definition in the Python standard library
- which should still work with our plugin however.
'''

from typing import Type, Optional, Callable, List, Union

from mypy import nodes, types
import mypy.plugin


# Define under which names the mypy plugin should be registered
REGISTER = {
	'fastenum.Enum',
	# To support for example :code:`controllers.orders.types.fastenum`
	'.fastenum',
	# Might hijack Enum from standard library but should still work as expected.
	# Unfortunately there is no other way to work with :code:`from fastenum import Enum`
	'Enum',
}

# This is needed to shadow some classes and ignore them by this plugin
BLACKLIST = {
	'FastEnumPlugin',
	'fastenum.mypy_plugin.Plugin',
}


def _is_fullname_supported(fullname: str) -> bool:
	'''
	Helper function to specify if given fullname is usable by our plugin
	'''
	for r in REGISTER:
		if r in fullname and fullname not in BLACKLIST:
			return True

	return False


def _define_method(
	context: Union[mypy.plugin.AnalyzeTypeContext, mypy.plugin.ClassDefContext],
	cls_info: nodes.TypeInfo,
	namespace: str,
	name: str,
	arguments: List[nodes.Argument],
	return_type: types.Type,
) -> None:
	'''
	Helper function to define class level or instance level method.
	If an instance-level method will be created, the user of this method is
	responsible for specifying :code:`self` as the first argument.

	This is basically a ripoff of https://github.com/python/mypy/blob/master/mypy/plugins/common.py#L80
	That implementation can't be directly used as it can create
	only an instance-level method (always adding :code:`self`). It is also not supported
	when defining new types in :code:`get_type_analyze_hook` hook
	(see available hooks: https://mypy.readthedocs.io/en/latest/extending_mypy.html#current-list-of-plugin-hooks)

	:param context: mypy plugin context used to interact with mypy API
	:param cls_info: :code:`TypeInfo` of class where this method should be bound
	:param namespace: used to build fullname of this method
	'''
	function_type: types.Instance
	if isinstance(context, mypy.plugin.ClassDefContext):
		function_type = context.api.named_type('__builtins__.function')
	elif isinstance(context, mypy.plugin.AnalyzeTypeContext):
		function_type = context.api.named_type('builtins.function')
	else:
		raise ValueError('Not supported context type = {}.'.format(type(context)))

	arg_types: List[Optional[types.Type]] = []
	arg_names: List[str] = []
	# Kinds are kind of arguments (position, key word,..) see :code:`nodes.ARG_POS` for example.
	arg_kinds: List[int] = []

	for arg in arguments:
		assert arg.type_annotation, 'All arguments must be fully typed.'
		arg_types.append(arg.type_annotation)
		arg_names.append(get_name(arg.variable))
		arg_kinds.append(arg.kind)

	# Creating type of a callable, this is equialent to writing
	# 	Callable[[arg_types,...], return_type]
	# in mypy typing system, except you have to specify arugment position type,
	# argument names (as when you will write real function)
	# And then last argument is fallback type :code:`function_type` which I don't know why is needed?
	signature = types.CallableType(arg_types, arg_kinds, arg_names, return_type, function_type)

	# Once we have our function type defined we also have to create an AST node.
	# This is needed so mypy knows that given function is bound to some class, module or something.
	# So when we call it it can find its type back.
	# The following line is equivalent to:
	# 	def <name>(<arguments>): pass
	# You can see it is without types - its just an AST node
	func = nodes.FuncDef(name, arguments, nodes.Block([nodes.PassStmt()]))
	# I don't know why but we have to add both links:
	# 	- From class to method (few lines later)
	# 	- And from method to class (maybe needed so it can be seen as a bound method?)
	func.info = cls_info
	# Specify method type (return type, argument types, ...)
	# it is taken from previous :code:`signature` callable type we defined and just named
	func.type = signature.with_name(name)
	# We have to define fullname - this is normally filled by mypy's AST parser
	# Fullname is required by mypy because this should be unique identifier for any object
	func._fullname = f'{namespace}.{name}'  # pylint: disable=protected-access
	# This should not be required but mypy is then able to say where the error is happening
	func.line = cls_info.line

	# And at last we have to register our method on our class (defined as a :code:`cls_info` object).
	# Every class have :code:`names` attribute which is :code:`SymbolTable` instance and defines
	# all attributes, methods.
	# Entries in this table are :code:`SymbolTableNode` where you have to specify first argument kind:
	# 	LDEF: local definition
	# 	GDEF: global (module-level) definition
	# 	MDEF: class member definition
	# 	UNBOUND_IMPORTED: temporary kind for imported names
	# Then the AST node which defines a variable or a function definition.
	# But this will just register that name on a given class but not that node to the AST of the class.
	cls_info.names[name] = nodes.SymbolTableNode(nodes.MDEF, func, plugin_generated = True)
	# To register our method or attribute in the class' AST we have to use the following line.
	# Beware that mypy can work even without registering this
	# but won't be able to perform some checks (don't know which exactly).
	cls_info.defn.defs.body.append(func)


def transform_enum_class_def(context: mypy.plugin.ClassDefContext) -> None:
	'''
	This is registered as a handler of the :code:`get_base_class_hook` hook
	See https://mypy.readthedocs.io/en/latest/extending_mypy.html#current-list-of-plugin-hooks for
	more information about hooks.

	It gives us the ability to get the defined type and AST nodes representing how
	mypy sees our class (inherited from Enum) and perform any changes we want.

	This hook is called after :code:`get_type_analyze_hook` hook which is handled by :code:`transform_enum_type`
	so the type of the super class is slightly altered after that.

	But if we inspect :code:`context.cls` which is of type :code:`nodes.ClassDef` (an AST node)
	we get something like:

	  ::
	    ClassDef:3(
	    Color
	    BaseType(_fastenum.Enum)
	    AssignmentStmt:4(
	      NameExpr(RED [m])
	      IntExpr(1)
	      builtins.int)
	    AssignmentStmt:5(
	      NameExpr(GREEN [m])
	      IntExpr(2)
	      builtins.int)
	    AssignmentStmt:6(
	      NameExpr(BLUE [m])
	      IntExpr(3)
	      builtins.int))

	And by inspecting :code:`context.cls.info` which is of type :code:`nodes.TypeInfo` - still an AST node
	but one which defines the type of our class (where previous defined class definition) you'll get:

	  ::
	    TypeInfo(
	      Name(main.Color)
	      Bases(_fastenum.Enum)
	      Mro(main.Color, _fastenum.Enum, builtins.object)
	      Names(
	        BLUE (builtins.int)
	        GREEN (builtins.int)
	        RED (builtins.int))
	      MetaclassType(_fastenum.EnumMeta))


	From these you can see that mypy sees our class attributes as int, str or any value our enum has
	but not as Enum class instances. So we have to update that.
	'''
	info = context.cls.info

	# This is a hotfix for the built-in `enum.Enum` class
	# which inherits from the default builtins.int
	# and therefor our comparison methods are not compatible.
	# To get over that we remove `int` base class from that class def.
	info.bases = [base for base in info.bases if get_fullname(base.type) != 'builtins.int']

	# First clear all `nodes.AssignmentStmt` in class.
	# These are basically class-level attributes defining enum values.
	# This way we will remove the attribute assignment statement
	# that mypy sees when the class is defined as:
	#
	# 	class Color(Enum):
	# 		RED = 1
	#
	# From this mypy creates :code:`AssignmentStmt` where RED = IntInstance(1)
	# We remove this, so mypy see only class variables in :code:`info.names`
	#
	# By this method we are only clearing the AST tree from our class definition
	# so it will look like: :code:`class <EnumName>(Enum): pass`
	#
	# But for example :code:`names` (attributes and methods) for our class will be still defined
	# also all inherited things will be still visible.
	# This way we can tell mypy that this class has some attributes, it just is not defined in AST
	context.cls.defs.body = [
		node
		for node in context.cls.defs.body
		if not isinstance(node, nodes.AssignmentStmt)
	]

	# Create common types handlers
	str_type = context.api.named_type('__builtins__.str')
	# When working with classes in mypy types
	# the only viable option is to use :code:`Instance` even for the class itself
	# because even class definition is instance of it's "type".
	self_type = types.Instance(info, [])

	metaclass_type = info.metaclass_type

	# Override __next__ and __getitem__ in the Enum class (for each subclass - each enum)
	# to properly say that its return values are children instances.
	#
	# This is needed as a little hack because we are defining :code:`__iter__` and :code:`__next__`
	# in :code:`transform_enum_type` handler using :code:`TypeVar` but
	# we are missing something and mypy does not see that when we have a specific implementation
	# it should return itself.
	# So as a hotfix we redefine these methods on our inherited enum class
	# to have more specific typing (which is still not violating original metaclass typing)
	_define_method(
		context,
		metaclass_type.type,
		get_fullname(metaclass_type.type),
		'__next__',
		[
			nodes.Argument(
				nodes.Var('self', self_type),
				self_type,
				None,
				nodes.ARG_POS,
			),
		],
		self_type,
	)

	# Example how these all lines below would look like in Python:
	#
	#  ::
	#    def __getitem__(cls: Type[<self_type>], key: str) -> <self_type>:
	#      pass
	#
	#
	_define_method(
		context,
		metaclass_type.type,
		get_fullname(metaclass_type.type),
		'__getitem__',
		[
			nodes.Argument(nodes.Var('cls', metaclass_type), metaclass_type, None, nodes.ARG_POS),
			nodes.Argument(nodes.Var('key', str_type), str_type, None, nodes.ARG_POS),
		],
		self_type,
	)

	# In the end we have to update type of our attributes to return the proper type.
	# So we go through all the names defined on the class
	# and filter out only type :code:`nodes.Var` which are class attributes.
	for name, named_node in info.names.items():
		# We want to modify only class an instance level variables
		if isinstance(named_node.node, nodes.Var):
			node: nodes.Var = info.names[name].node

			# We replace original type (which will be int, str, ...)
			# with an ``Instance`` of our class itself (not with the base class - Enum).
			node.type = types.Instance(info, [])
			# We also want to make sure these variables are class-level so you can call
			# something like :code:`Color.RED`
			node.is_initialized_in_class = True

			# In the end assign it back and mark it as generated by plugin
			info.names[name] = nodes.SymbolTableNode(nodes.MDEF, node, plugin_generated = True)
			# TODO Maybe in future we can add AST definition back
			# TODO but to do this we have to define creation of new instance for every
			# TODO assignment or somewhere take right part of assignment.
			#
			# info.defn.defs.body.append(
			# 	AssignmentStmt(
			# 		[NameExpr(name)],
			# 		cls.info,
			# 		order_other_type
			# 	)
			# )


def transform_enum_type(context: mypy.plugin.AnalyzeTypeContext) -> types.Type:
	'''
	This is registered as a handler of the :code:`get_type_analyze_hook` hook
	See https://mypy.readthedocs.io/en/latest/extending_mypy.html#current-list-of-plugin-hooks for
	more information about hooks.

	This will be the first hook called in this plugin.
	It allows us to change or alter type definitions as mypy sees them.

	This is needed because our Enum uses :code:`EnumMeta` class which defines :code:`__new__`
	and it shadows everything for mypy. So without this callback our type
	visible by mypy would look like following in :code:`transform_enum_class_def`
	hook handler. (Please read comment in :code:`transform_enum_class_def`).

	  ::
	    ClassDef:3(
	      Color
	      FallbackToAny
	      AssignmentStmt:4(
	        NameExpr(RED [m])
	        IntExpr(1)
	        builtins.int)
	      AssignmentStmt:5(
	        NameExpr(GREEN [m])
	        IntExpr(2)
	        builtins.int)
	      AssignmentStmt:6(
	        NameExpr(BLUE [m])
	        IntExpr(3)
	        builtins.int))

	    TypeInfo(
	      Name(main.Color)
	      Bases(builtins.object)
	      Mro(main.Color, builtins.object)
	      Names(
	        BLUE (builtins.int)
	        GREEN (builtins.int)
	        RED (builtins.int)))

	Please check docs to :code:`transform_enum_class_def` method
	where you can se what these types look like when this hook
	is used.

	For example you can see that :code:`TypeInfo` is completely missing
	  - inheritance to Enum type
	  - metaclass definition

	So it would be possible to change attribute types without this hook
	but we have to add a metaclass for :code:`__iter__` and :code:`__getitem__` definitions.

	To do this we have to describe what :code:`Enum` and :code:`EnumMeta` look like.
	We did not find how to tell mypy to give us these definitions
	so we described them manually in this part, and then properly
	added inheritance to any :code:`Enum` inherited class to our fake :code:`Enum`.
	'''
	# Find references to some builtins which are often used
	type_type = context.api.named_type('builtins.type')
	object_type = context.api.named_type('builtins.object')
	str_type = context.api.named_type('builtins.str')
	bool_type = context.api.named_type('builtins.bool')

	# Define meta class in fake module :code:`_fastenum`
	# This is roughly equivalent to:
	#
	# 	class EnumMeta(builtins.type): pass
	#
	# in module :code:`_fastenum`.
	# We have to define two things: :code:`ClassDef` an AST node and it's type definition using :code:`TypeInfo`
	# :code:`ClassDef` defines only class syntactically all attributes and such will be defined in :code:`TypeInfo`
	meta_cls = nodes.ClassDef('EnumMeta', nodes.Block([nodes.PassStmt()]), [], [type_type])
	meta_cls.fullname = '_fastenum.EnumMeta'
	meta_info = nodes.TypeInfo(nodes.SymbolTable(), meta_cls, '_fastenum')
	# We have to define inheritance again, mypy :code:`ClassDef` and :code:`TypeInfo`
	# won't automatically share this information so we have to tell it again it is inherited from :code:`builtins.type`
	meta_info.bases = [type_type]
	# Last thing to get everything working is to define mro (method resolution order)
	# without correctly specifying this mypy won't complain but it wont see any method or attributes
	# defined in parents or even class itself.
	# So we have to define class itself as :code:`meta_info` and **all of it's parents** (even indirect one)
	# (this is not working in transitional fashion)
	meta_info.mro = [meta_info, type_type.type, object_type.type]

	# Define Enum class which is using EnumMeta as its metaclass in the fake :code:`_fastenum` module
	# This is very similar to the previous definition and it is roughly equivalent to:
	#
	# 	class Enum(metaclass = EnumMeta): pass
	#
	# Notice that we still define :code:`builtins.object` as it's parent
	# even if we don't have to do it in Python3, but we have to do it here!
	enum_cls = nodes.ClassDef(
		'Enum',
		nodes.Block([nodes.PassStmt()]),
		[],
		[object_type],
		nodes.NameExpr('EnumMeta'),
	)
	enum_cls.fullname = '_fastenum.Enum'
	enum_info = nodes.TypeInfo(nodes.SymbolTable(), enum_cls, '_fastenum')
	# Same as before we have to define all parents (even :code:`builtins.object`)
	enum_info.bases = [object_type]
	enum_info.mro = [enum_info, object_type.type]
	# New things in here are that we have to define the metaclass again in info
	# I don't know why we have to define it on :code:`metaclass_type` and :code:`declared_metaclass`
	# at the same time but mypy requires it that way, otherwise it ignores that metaclass
	# and does not complain at all.
	enum_info.metaclass_type = types.Instance(meta_info, [])
	enum_info.declared_metaclass = types.Instance(meta_info, [])

	# Add the attribute ``value`` to enum instances.
	# Don't be scared by :code:`TypeOfAny`. mypy just has multiple types of Any.
	# See :code:`TypeOfAny` definition (it is an enum with comments).
	value_attribute = nodes.Var('value', types.AnyType(types.TypeOfAny.explicit))
	value_attribute.is_initialized_in_class = False
	# As before we have to link our variable back to our class.
	value_attribute.info = enum_info
	enum_info.names['value'] = nodes.SymbolTableNode(nodes.MDEF, value_attribute, plugin_generated = True)

	# Add the attribute ``name``` to enum instances.
	value_attribute = nodes.Var('name', str_type)
	value_attribute.is_initialized_in_class = False
	value_attribute.info = enum_info
	enum_info.names['name'] = nodes.SymbolTableNode(nodes.MDEF, value_attribute, plugin_generated = True)

	#
	# So after these few lines we end up with something like:
	#
	# module `_fastenum`:
	#
	# 	class EnumMeta(builtins.type): pass
	#
	# 	class Enum(metaclass = EnumMeta):
	# 		name: str
	# 		value: Any
	#

	# Prepare TypeVar, all these lines are just:
	# 	_EnumMetaType = TypeVar('_EnumMetaType', bound = 'EnumMeta')
	# We just have to describe expressions and definitions separately for mypy
	meta_enum_instance = types.Instance(meta_info, [])
	self_tvar_expr = nodes.TypeVarExpr(
		'_EnumMetaType',
		f'{get_fullname(meta_info)}._EnumMetaType',
		[],
		meta_enum_instance,
	)
	meta_info.names['_EnumMetaType'] = nodes.SymbolTableNode(nodes.MDEF, self_tvar_expr)

	self_tvar_def = types.TypeVarDef(
		'_EnumMetaType',
		f'{get_fullname(meta_info)}._EnumMetaType',
		-1,
		[],
		meta_enum_instance,
	)
	self_tvar_type = types.TypeVarType(self_tvar_def)

	# Same way with __getitem__
	_define_method(
		context,
		meta_info,
		context.type.name,
		'__getitem__',
		[
			nodes.Argument(nodes.Var('cls', meta_enum_instance), meta_enum_instance, None, nodes.ARG_POS),
			nodes.Argument(nodes.Var('key', str_type), str_type, None, nodes.ARG_POS),
		],
		self_tvar_type,
	)

	# We also have to support constructor interface of enum, so when someone calls Enum('value').
	# This is simply done by adding the `__init__` method with two arguments (self, value).
	enum_instance = types.Instance(enum_info, [])
	any_type = types.AnyType(types.TypeOfAny.explicit)
	_define_method(
		context,
		enum_info,
		context.type.name,
		'__init__',
		[
			nodes.Argument(nodes.Var('self', enum_instance), enum_instance, None, nodes.ARG_POS),
			nodes.Argument(nodes.Var('value', any_type), any_type, None, nodes.ARG_POS),
		],
		types.NoneTyp(),
	)

	# Define base __iter__ and __next__ for our meta class and use TypeVar `_EnumMetaType` as its return value
	# so we can say its return value is bound to all children.
	# See more comments about the definition in:
	# 	- `transform_enum_class_def` handler
	# 	- and `_define_method` docs + comments
	_define_method(
		context,
		meta_info,
		context.type.name,
		'__iter__',
		[
			nodes.Argument(
				nodes.Var('self', meta_enum_instance),
				meta_enum_instance,
				None,
				nodes.ARG_POS,
			),
		],
		self_tvar_type,
	)
	_define_method(
		context,
		meta_info,
		context.type.name,
		'__next__',
		[
			nodes.Argument(
				nodes.Var('self', meta_enum_instance),
				meta_enum_instance,
				None,
				nodes.ARG_POS,
			),
		],
		self_tvar_type,
	)

	# Because enums can be used even in comparison expression like `A > B`
	# we have to support these methods in our fake enum class too.
	def def_bool_method(name: str) -> None:
		_define_method(
			context,
			enum_info,
			context.type.name,
			name,
			[
				nodes.Argument(nodes.Var('self', enum_instance), enum_instance, None, nodes.ARG_POS),
				nodes.Argument(nodes.Var('other', enum_instance), enum_instance, None, nodes.ARG_POS),
			],
			bool_type,
		)

	for name in ('le', 'eq', 'ne', 'ge', 'gt'):
		def_bool_method(f'__{name}__')

	#
	# After all this we end up with:
	#
	# module `_fastenum`:
	#
	# 	class EnumMeta(builtins.type):
	# 		_EnumMetaType = TypeVar('_EnumMetaType', bound = 'EnumMeta')
	#
	# 		def __iter__(self: 'EnumMeta') -> _EnumMetaType: pass
	# 		def __next__(self: 'EnumMeta') -> _EnumMetaType: pass
	# 		def __getitem__(cls: 'EnumMeta', key: str) -> 'EnumMeta': pass
	#
	#
	# 	class Enum(metaclass = EnumMeta):
	# 		name: str
	# 		value: Any
	#
	# 		def __init__(self, value: Any) -> None: pass
	#
	# 		def __le__(self, other: Enum) -> bool: pass
	# 		def __eq__(self, other: Enum) -> bool: pass
	# 		def __ne__(self, other: Enum) -> bool: pass
	# 		def __ge__(self, other: Enum) -> bool: pass
	# 		def __gt__(self, other: Enum) -> bool: pass
	#
	# And we have to return new type for our `Enum` class which will be our new `Enum`
	return types.Instance(enum_info, [])


class FastEnumPlugin(mypy.plugin.Plugin):
	'''
	Register Fastenum plugin for all classes
	inheriting from Fastenum base class.

	See docs in https://mypy.readthedocs.io/en/latest/extending_mypy.html
	'''

	def get_base_class_hook(self, fullname: str) -> Optional[Callable[[mypy.plugin.ClassDefContext], None]]:
		'''
		Register callback for class definition so we can
		properly describe how enum class/instance behave.
		'''
		if _is_fullname_supported(fullname):
			return transform_enum_class_def

		return super().get_base_class_hook(fullname)

	def get_type_analyze_hook(
		self,
		fullname: str,
	) -> Optional[Callable[[mypy.plugin.AnalyzeTypeContext], types.Type]]:
		'''
		Mypy does not see metaclass usage on the Enum side properly
		for this we have to re-define these types in our plugin.
		'''
		if _is_fullname_supported(fullname):
			return transform_enum_type

		return super().get_type_analyze_hook(fullname)


def plugin(_: str) -> Type[FastEnumPlugin]:
	'''
	Define plugins for different mypy versions
	'''
	# The first argument (mypy version) can be used to have multiple versions of this
	# plugin for multiple mypy versions.
	return FastEnumPlugin


# Taken and modified from https://github.com/samuelcolvin/pydantic/pull/1058/files#diff-ef0fe5e1687ec75a3d9fe2d593799ff8R669
def get_fullname(x: Union[nodes.FuncBase, nodes.SymbolNode]) -> str:
	'''
	Used for compatibility with mypy 0.740; can be dropped once support for 0.740 is dropped.
	'''
	fn = x.fullname
	return fn() if callable(fn) else fn


def get_name(x: Union[nodes.FuncBase, nodes.SymbolNode]) -> str:
	'''
	Used for compatibility with mypy 0.740; can be dropped once support for 0.740 is dropped.
	'''
	fn = x.fullname
	return fn() if callable(fn) else fn
