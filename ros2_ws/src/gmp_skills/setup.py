import os
from glob import glob
from setuptools import find_packages, setup

package_name = 'gmp_skills'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    tests_require=['pytest'],
    zip_safe=True,
    maintainer='Kim Byeongjik',
    maintainer_email='jungji0913@khu.ac.kr',
    description='로봇 스킬 서버 — DSR_ROBOT2·RG2 어댑터, 스테이션 이동·파지·스쿱·붓기·계량',
    license='MIT',
    entry_points={
        'console_scripts': [
            'skill_node = gmp_skills.nodes.skill_node:main',
        ],
    },
)
