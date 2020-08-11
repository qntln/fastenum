import os
from setuptools import setup


def read(relpath: str) -> str:
	with open(os.path.join(os.path.dirname(__file__), relpath)) as f:
		return f.read()


setup(
	name = 'fastenum',
	version = read('version.txt').strip(),
	description = "Faster drop-in replacement of Python's enum",
	long_description = read('README.rst'),
	author = 'Quantlane',
	author_email = 'code@quantlane.com',
	url = 'https://github.com/qntln/fastenum',
	license = 'Apache 2.0',
	packages = [
		'fastenum',
	],
	package_data = {
		'fastenum': [
			'py.typed'
		],
	},
	classifiers = [
		'Development Status :: 4 - Beta',
		'License :: OSI Approved :: Apache Software License',
		'Natural Language :: English',
		'Programming Language :: Python :: 3 :: Only',
		'Programming Language :: Python :: 3.5',
	],
)
