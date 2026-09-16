from setuptools import find_packages, setup

package_name = 'gmp_process'

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
    description='레시피 실행 상태기계, 일탈·인터락, RunBatch 서버',
    license='MIT',
    entry_points={'console_scripts': ['process_node = gmp_process.nodes.process_node:main']},
)
