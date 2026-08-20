#!/usr/bin/env python3
########################################################################
#
# Copyright 2025 Volker Muehlhaus and IHP PDK Authors
#
# Licensed under the GNU General Public License, Version 3.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    https://www.gnu.org/licenses/gpl-3.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
########################################################################

# Rewrite relative links/images in README.md to absolute GitHub URLs, so they
# render correctly on the PyPI package page (PyPI has no access to files
# outside the built distribution). Writes README_pypi.md at repo root, which
# pyproject.toml's readme= key points at. Regenerate before every build.

import os
import re
import tomllib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_BASE = "https://raw.githubusercontent.com/VolkerMuehlhaus/setupEM/main/"

def is_relative(path):
    return not (path.startswith('http://') or path.startswith('https://') or path.startswith('#'))

def rewrite_relative_links(text):
    def replace_md(match):
        prefix, path = match.group(1), match.group(2)
        if not is_relative(path):
            return match.group(0)
        return f"{prefix}({RAW_BASE}{path.removeprefix('./')})"

    text = re.sub(r'(!?\[[^\]]*\])\(([^)]+)\)', replace_md, text)

    def replace_img(match):
        path = match.group(1)
        if not is_relative(path):
            return match.group(0)
        return f'src="{RAW_BASE}{path.removeprefix("./")}"'

    text = re.sub(r'src="([^"]+)"', replace_img, text)

    return text

def package_requirements_note():
    # README.md describes the whole repo/workflow, which is broader than what
    # the installed package itself needs. Append an accurate note derived
    # straight from pyproject.toml's dependencies, so the PyPI page can't
    # drift from what pip actually installs.
    with open(os.path.join(REPO_ROOT, 'pyproject.toml'), 'rb') as f:
        project = tomllib.load(f)['project']

    deps = '\n'.join(f'- {d}' for d in project['dependencies'])
    return (
        f"\n\n---\n\n**Note:** the `{project['name']}` PyPI package itself requires:\n\n"
        f"{deps}\n\n"
        "(Other Python modules mentioned above are only needed to run standalone helper "
        "scripts in this repository, not to use the installed package.)\n"
    )

def main():
    src_path = os.path.join(REPO_ROOT, 'README.md')
    dst_path = os.path.join(REPO_ROOT, 'README_pypi.md')

    with open(src_path, 'r', encoding='utf-8') as f:
        text = f.read()

    text = rewrite_relative_links(text)
    text += package_requirements_note()

    with open(dst_path, 'w', encoding='utf-8') as f:
        f.write(text)

    print(f'Wrote {dst_path}')

if __name__ == '__main__':
    main()
