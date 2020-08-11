from typing import Union, Type

import enum

import pytest
import pytest_benchmark.fixture

import fastenum


class Enum(enum.Enum):
	A = 1
	B = 2
	C = 3
	D = 4


class FastEnum(fastenum.Enum):
	A = 1
	B = 2
	C = 3
	D = 4


EnumClasses = Union[Type[Enum], Type[FastEnum]]


parametrize_enum_classes = pytest.mark.parametrize(
	'enum_class',
	(Enum, FastEnum),
	ids = ('enum', 'fastenum'),
)


@parametrize_enum_classes
def test_attribute_access(
	enum_class: EnumClasses,
	benchmark: pytest_benchmark.fixture.BenchmarkFixture,
) -> None:
	def test() -> None:
		enum_class.C  # type: ignore # pylint: disable=pointless-statement

	benchmark(test)


@parametrize_enum_classes
def test_getitem_access(
	enum_class: EnumClasses,
	benchmark: pytest_benchmark.fixture.BenchmarkFixture,
) -> None:
	def test() -> None:
		enum_class['C']  # type: ignore # pylint: disable=pointless-statement

	benchmark(test)


@parametrize_enum_classes
def test_call(
	enum_class: EnumClasses,
	benchmark: pytest_benchmark.fixture.BenchmarkFixture,
) -> None:
	def test() -> None:
		enum_class(3)  # type: ignore

	benchmark(test)


@parametrize_enum_classes
def test_iter(
	enum_class: EnumClasses,
	benchmark: pytest_benchmark.fixture.BenchmarkFixture,
) -> None:
	def test() -> None:
		list(enum_class)  # type: ignore

	benchmark(test)
