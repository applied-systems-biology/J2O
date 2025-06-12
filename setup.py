from setuptools import setup, find_packages

setup(
    name='JIPipeRunner',
    version='0.0.1',
    description="OMERO plugin to run JIPipe",
    packages=find_packages(),
    include_package_data=True,
    keywords=['omero', 'jipipe'],
)
