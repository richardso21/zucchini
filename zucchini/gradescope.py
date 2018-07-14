"""
Utilities for gradescope autograding.
"""

import os
import json
from zipfile import ZipFile, ZIP_DEFLATED

from .constants import ASSIGNMENT_CONFIG_FILE, ASSIGNMENT_FILES_DIRECTORY
from .utils import ConfigDictMixin, ConfigDictNoMangleMixin, \
                   datetime_from_string


class GradescopeMetadata(object):
    """
    Parse the metadata as described in:
    https://gradescope-autograders.readthedocs.io/en/latest/submission_metadata/
    """

    _ATTRS = {
        'id': int,
        'created_at': datetime_from_string,
        'assignment_id': int,
    }

    def __init__(self, json_dict):
        for attr, type_ in self._ATTRS.items():
            setattr(self, attr, type_(json_dict[attr]))

    @classmethod
    def from_json_path(cls, json_path):
        with open(json_path, 'r') as json_fp:
            return cls(json.load(json_fp))


class GradescopeAutograderTestOutput(ConfigDictNoMangleMixin, ConfigDictMixin):
    """
    Output of a single test in Gradescope JSON.
    """

    def __init__(self, name=None, score=None, max_score=None, output=None):
        self.score = float(score) if score is not None else None
        self.max_score = float(max_score) if max_score is not None else None
        self.output = output


class GradescopeAutograderOutput(ConfigDictNoMangleMixin, ConfigDictMixin):
    """
    Hold Gradescope Autograder output as described in
    https://gradescope-autograders.readthedocs.io/en/latest/specs/#output-format
    """

    def __init__(self, score=None, tests=None, extra_data=None):
        self.score = score
        self.tests = [GradescopeAutograderTestOutput.from_config_dict(test)
                      for test in tests] if tests is not None else None
        self.extra_data = extra_data

    def to_config_dict(self, *args):
        dict_ = super(GradescopeAutograderOutput, self).to_config_dict(*args)
        if dict_.get('tests', None):
            dict_['tests'] = [test.to_config_dict() for test in dict_['tests']]
        return dict_

    @classmethod
    def from_grade(cls, grade):
        """
        Convert a grading_manager.Grade to Gradescope JSON.
        """

        score = grade.score()
        tests = []
        # Store the component grades in the extra_data field
        extra_data = grade.serialized_component_grades()

        computed_grade = grade.computed_grade()

        # Add penalties
        for penalty in computed_grade.penalties:
            if penalty.points_delta != 0:
                test = GradescopeAutograderTestOutput(
                    name=penalty.name,
                    score=grade.to_float(penalty.points_delta))
                tests.append(test)

        # Add actual test results
        for component in computed_grade.components:
            for part in component.parts:
                if part.deductions:
                    deductions = 'Deductions: {}\n\n'.format(
                        ', '.join(part.deductions))
                else:
                    deductions = ''

                test = GradescopeAutograderTestOutput(
                    name='{}: {}'.format(component.name, part.name),
                    score=grade.to_float(part.points_got),
                    max_score=grade.to_float(part.points_possible),
                    output=deductions + part.log)
                tests.append(test)

        return cls(score=score, tests=tests, extra_data=extra_data)

    def to_json_stream(self, fp):
        json.dump(self.to_config_dict(), fp)


SETUP_SH = r'''#!/bin/bash
set -e

apt-get update
apt-get install -y python3 python3-pip
pip3 install zucchini
'''


RUN_AUTOGRADER = r'''#!/bin/bash
set -e
set -o pipefail

cd /autograder/source
zucc grade-submission /autograder/submission \
    | zucc gradescope bridge /autograder/submission_metadata.json \
    > /autograder/results/results.json
'''


class GradescopeAutograderZip(object):
    """
    Generates a Gradesope autograder zip file from which Gradescope
    generates a Docker image for grading.
    """

    def __init__(self, path='.'):
        self.path = path

    def _relative_path(self, abspath):
        """
        Convert an absolute path to an assignment file to a path
        relative to self.path.
        """
        return os.path.relpath(abspath, self.path)

    def _real_path(self, relpath):
        """
        Convert a relative path to an assignment file to an absolute
        path.
        """
        return os.path.join(self.path, relpath)

    def _write_file(self, file_path, zipfile):
        """
        Add a file to the generated zip file. file_path should be relative to
        self.path.
        """
        real_path = self._real_path(file_path)
        zipfile.write(real_path, file_path)

    def _write_string(self, string, path, zipfile):
        """
        Add a file to the generated zip file. file_path should be relative to
        self.path.
        """
        zipfile.writestr(path, string)

    def _write_dir(self, dir_path, zipfile):
        """
        Recursively add a directory to the generated zip file. dir_path
        should be relative to self.path.
        """

        real_path = self._real_path(dir_path)

        for dirpath, _, filenames in os.walk(real_path):
            for filename in filenames:
                relpath = self._relative_path(os.path.join(dirpath, filename))
                self._write_file(relpath, zipfile)

    def write_zip(self, file):
        """
        Write the autograder .zip to file. If file is a file-like
        object, write it there, otherwise it should be a string
        designating the destination path.
        """

        with ZipFile(file, 'w', ZIP_DEFLATED) as zipfile:
            self._write_file(ASSIGNMENT_CONFIG_FILE, zipfile)

            grading_files = self._real_path(ASSIGNMENT_FILES_DIRECTORY)
            if os.path.exists(grading_files):
                self._write_dir(ASSIGNMENT_FILES_DIRECTORY, zipfile)

            self._write_string(SETUP_SH, 'setup.sh', zipfile)
            self._write_string(RUN_AUTOGRADER, 'run_autograder', zipfile)
