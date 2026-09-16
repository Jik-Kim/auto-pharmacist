from setuptools import find_packages, setup

package_name = 'gmp_dosing'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Kim Byeongjik',
    maintainer_email='jungji0913@khu.ac.kr',
    description='힘·작업물무게 → 그램(영점·보정), 이중 폐루프 도징 정책 — ROS 비의존',
    license='MIT',
)
