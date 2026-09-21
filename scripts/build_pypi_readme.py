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

# Build a short PyPI package-page README instead of dumping the full,
# screenshot-heavy repo README.md into the long_description: title + intro
# (reused verbatim from README.md) + install line + a link to GitHub for full
# docs + the last few dated entries from doc/CHANGES.md, so PyPI visitors can
# see what actually changed without digging through the whole README. Writes
# README_pypi.md at repo root, which pyproject.toml's readme= key points at.
# Regenerate before every build.

import os
import re
import tomllib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_BASE = "https://raw.githubusercontent.com/VolkerMuehlhaus/setupEM/main/"
REPO_URL = "https://github.com/VolkerMuehlhaus/setupEM"

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

def extract_intro(readme_text):
    # title is the first line; README.md's very next section is "## What's
    # New" (a hand-curated highlights list, redundant with the Recent
    # changes excerpt below), so skip it and use the following section's
    # body - the actual one-paragraph tool description - as the intro
    lines = readme_text.splitlines()
    title = lines[0]
    rest = '\n'.join(lines[1:])
    chunks = [c for c in re.split(r'\n(?=#)', rest) if c.strip()]
    for chunk in chunks:
        chunk_lines = chunk.splitlines()
        heading = chunk_lines[0].lstrip('#').strip().lower()
        if heading == "what's new":
            continue
        return title, '\n'.join(chunk_lines[1:]).strip()
    return title, ''

def extract_recent_changes(n=3, heading_level=1):
    # doc/CHANGES.md has no separate title line - it starts directly with
    # dated entries as "# What's New - <date>" headings
    with open(os.path.join(REPO_ROOT, 'doc', 'CHANGES.md'), 'r', encoding='utf-8') as f:
        text = f.read()

    marker = '#' * heading_level + ' '
    chunks = re.split(rf'\n(?={re.escape(marker)})', text)
    entries = [c.strip() for c in chunks if c.strip().startswith(marker)]
    return '\n\n'.join(entries[:n])

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
        readme_text = f.read()

    with open(os.path.join(REPO_ROOT, 'pyproject.toml'), 'rb') as f:
        project_name = tomllib.load(f)['project']['name']

    title, intro = extract_intro(readme_text)
    recent_changes = extract_recent_changes(n=3)
    changelog_url = f"{REPO_URL}/blob/main/doc/CHANGES.md"

    parts = [
        title,
        rewrite_relative_links(intro),
        f'## Install\n\n    pip install {project_name}',
        f'**Full documentation, installation guide, and usage walkthrough:**\n{REPO_URL}',
        '## Recent changes',
        rewrite_relative_links(recent_changes),
        f'Full history: [CHANGES.md]({changelog_url})',
    ]
    text = '\n\n'.join(parts)
    text += package_requirements_note()

    with open(dst_path, 'w', encoding='utf-8') as f:
        f.write(text)

    print(f'Wrote {dst_path}')

if __name__ == '__main__':
    main()
