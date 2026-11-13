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

"""
setupEM package
A Python tool for EM setup using gds2palace.
"""



def main():
    """Start the setupEM GUI. Imported here only when called, so that the modules without Qt (join_pieces,
    thermal_results, script_model, ...) can be imported without loading it."""
    from .setupEM import main as _main
    return _main()


__all__ = ["main"]
__version__ = "0.18.2"