import os
import time
from PyQt6.QtWidgets import QDialog, QFileDialog
from main_ui import Ui_Dialog  # Import from generated UI file
from find_bird_util import log_message, run_super_picky


class MainWindow(QDialog, Ui_Dialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        # self.setWindowTitle("Test Window")
        # self.setGeometry(100, 100, 600, 400)

        self.setupUi(self)

        self.directoryPath = ""

        # Setup connections
        self.browse_dir_button.clicked.connect(self.on_browse_button_clicked)
        self.confirm_button.accepted.connect(self.accept)
        self.confirm_button.rejected.connect(self.reject)

    def on_browse_button_clicked(self):
        # Open a dialog to select a directory
        directory = QFileDialog.getExistingDirectory(self, "Select Directory")
        if os.path.exists(directory):
            self.directoryPath = directory
            self.display_dir_box.setText(directory)
            print(f"Selected directory: {self.directoryPath}")

        return None

    def accept(self):
        # Code to run when the confirm button is accepted
        if not os.path.exists(self.directoryPath):
            return None

        start = time.time()
        run = run_super_picky(self.directoryPath)
        end = time.time()

        log_message(f"Processing time: {end - start}, run = {run}", self.directoryPath)

        return None

    def reject(self):
        self.directoryPath = ""
        self.display_dir_box.setText("")
        # Code for cancelling the process
        print("Process cancelled.")
        return None
