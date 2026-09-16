import os
from glob import glob
from setuptools import find_packages, setup

package_name = 'gmp_hmi'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'config'), glob('config/*.sql')),
        (os.path.join('share', package_name, 'templates'), glob('templates/*.html')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Kim Byeongjik',
    maintainer_email='jungji0913@khu.ac.kr',
    description='웹 HMI(Flask)와 배치 기록(SQLite)',
    license='MIT',
    entry_points={'console_scripts': [
        'hmi_web_node = gmp_hmi.nodes.hmi_web_node:main',
        'record_node = gmp_hmi.nodes.record_node:main',
    ]},
)
