from setuptools import setup, find_packages

setup(
    name='J2O',
    version='1.0',
    description="OMERO plugin to run JIPipe",
    packages=find_packages(),
    include_package_data=True,
    keywords=['omero', 'jipipe'],
)
