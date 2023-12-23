import os
import time
from PyQt6.QtWidgets import QDialog, QFileDialog, QRadioButton, QButtonGroup

from Worker import Worker
from main_ui import Ui_Dialog  # Import from generated UI file
from find_bird_util import log_message, run_super_picky, delete_directory
import sys
from Stream import Stream


class MainWindow(QDialog, Ui_Dialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.processed_files = set()

        self.stream = Stream(newText=self.onUpdateText)
        sys.stdout = self.stream
        self.setupUi(self)

        self.directoryPath = ""

        # Setup connections
        self.browse_dir_button.clicked.connect(self.on_browse_button_clicked)
        self.confirm_button.accepted.connect(self.accept)
        self.confirm_button.rejected.connect(self.reject)

        self.progressBar.setValue(0)
        self.progressBar.setMaximum(100)
        self.progressBar.setMinimum(0)

    def onUpdateText(self, text):
        self.processing_txt_box.append(text)

    def on_browse_button_clicked(self):
        # Open a dialog to select a directory
        directory = QFileDialog.getExistingDirectory(self, "Select Directory")
        if os.path.exists(directory):
            self.directoryPath = directory
            self.display_dir_box.setText(directory)
            print(f"Selected directory: {self.directoryPath}")

        return None

    def accept(self):
        self.processed_files.clear()
        if not os.path.exists(self.directoryPath):
            return None

        start = time.time()

        # First, run the run_super_picky function and wait for it to complete
        if run_super_picky(self.directoryPath):
            # Then, start the processing which includes Worker thread
            self.startProcessing()

            # If you want to wait for the Worker to finish in this method:
            self.worker.finishedProcessing.connect(self.onWorkerFinished)

        end = time.time()
        log_message(f"Processing time: {end - start}", self.directoryPath)

        return None

    def reject(self):
        self.worker.quit()
        self.directoryPath = ""
        self.display_dir_box.setText("")
        # Code for cancelling the process
        print("Process cancelled.")
        return None

    def startProcessing(self):
        self.ui_settings = self.getCurrentSelections()
        self.worker = Worker(self.directoryPath, self.processed_files, self.ui_settings)
        self.worker.updateProgress.connect(self.updateProcessBar)
        self.worker.start()

    def updateProcessBar(self, value):
        self.progressBar.setValue(value)

    def onWorkerFinished(self):
        # Code to execute after the Worker thread has finished
        delete_directory(os.path.join(self.directoryPath, "Resized"))
        self.worker.quit()

## add comment
