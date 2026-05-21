#!/usr/bin/env python
# -*- coding: utf-8 -*-
##############################################
#
# This is open source software licensed under the Apache License 2.0
# http://www.apache.org/licenses/LICENSE-2.0
#
##############################################
"""Setup for plantgateway."""
from pathlib import Path

from setuptools import setup
from plantgw import __version__


def readme():
    """Load the readme file."""
    return Path('README.md').read_text(encoding='utf-8')


setup(
    name='plantgateway',
    version=__version__,
    description='Bluetooth to mqtt gateway for Xiaomi Mi plant sensors',
    long_description=readme(),
    long_description_content_type='text/markdown',
    author='Christian Kühnel',
    author_email='christian.kuehnel@gmail.com',
    url='https://github.com/ChristianKuehnel/plantgateway',
    packages=['plantgw'],
    python_requires='>=3.8',
    install_requires=[
        'bluepy==1.3.0',
        'miflora>=0.7.2,<0.8',
        'paho-mqtt>=1.6,<3',
        'PyYAML>=6.0.3',
    ],
    scripts=['plantgateway'],
    )
