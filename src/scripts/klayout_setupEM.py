import pya
import subprocess
import os
import shutil

# Derived from IHP EMStudio klayout script
# https://github.com/IHP-GmbH/EMStudio/blob/main/scripts/klEmsDriver.py


# Try to locate setupEM in system PATH
setupEM = shutil.which("setupEM.bat")
if setupEM ==None:
    setupEM = shutil.which("setupEM")


# Handler to launch setupEM with arguments
def handler_func():
    mw = pya.Application.instance().main_window()
    view = mw.current_view()

    # Check if layout is loaded
    if not view or not view.active_cellview().layout():
        pya.MessageBox.critical("Error", "No layout is currently loaded in KLayout.", pya.MessageBox.Ok)
        return

    # Get GDS file path and top cell name
    cell_view = view.active_cellview()
    gds_filename = cell_view.filename()

    simcfg_filename = gds_filename.replace(".gds",".simcfg")

    env = os.environ.copy()
    env.pop("PYTHONHOME", None) 

    # Build command line arguments
    if os.path.isfile(simcfg_filename):
        args = [
            setupEM,
            "-simcfg", simcfg_filename
        ]
    else:    
        args = [
            setupEM,
            "-gdsfile", gds_filename
        ]

    # For debugging – show constructed arguments
    # pya.MessageBox.info("Launch Info", f"GDS file: {gds_filename}\n\nArguments:\n{' '.join(args)}", pya.MessageBox.Ok)

    if setupEM != None:
        try:
            subprocess.Popen(args, env=env)
        except Exception as e:
            pya.MessageBox.critical("Error", f"Failed to launch setupEM:\n{str(e)}", pya.MessageBox.Ok)
    else:
        pya.MessageBox.critical("Error", f"setupEM not found in PATH", pya.MessageBox.Ok)

# Register menu action
menu_handler = pya.Action()
menu_handler.title = "setupEM"
menu_handler.on_triggered = handler_func

menu = pya.Application.instance().main_window().menu()
menu.insert_item("tools_menu.end", "menu_item_setupEM", menu_handler)
