"""
舒曼波共振遥感矿产预测系统 - 安装脚本
"""

from setuptools import setup, find_packages
from pathlib import Path

# 读取 README
readme_file = Path(__file__).parent / 'README.md'
long_description = ''
if readme_file.exists():
    long_description = readme_file.read_text(encoding='utf-8')

# 读取依赖
requirements_file = Path(__file__).parent / 'requirements.txt'
requirements = []
if requirements_file.exists():
    requirements = [
        line.strip()
        for line in requirements_file.read_text(encoding='utf-8').split('\n')
        if line.strip() and not line.startswith('#')
    ]

setup(
    name='schumann-mineral-prediction',
    version='1.0.0',
    author='Mineral Prediction Team',
    author_email='contact@example.com',
    description='基于舒曼波共振的遥感矿产预测系统',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/example/schumann-mineral-prediction',
    packages=find_packages(),
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Science/Research',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Topic :: Scientific/Engineering :: GIS',
        'Topic :: Scientific/Engineering :: Image Processing',
    ],
    python_requires='>=3.8',
    install_requires=requirements,
    extras_require={
        'dev': [
            'pytest>=6.2.0',
            'black>=21.0.0',
            'flake8>=3.9.0',
            'mypy>=0.910',
        ],
        'viz': [
            'plotly>=5.0.0',
            'folium>=0.12.0',
        ],
    },
    entry_points={
        'console_scripts': [
            'mineral-prediction=main:cli',
        ],
    },
    include_package_data=True,
    zip_safe=False,
)
