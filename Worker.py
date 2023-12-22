import os

from PyQt6 import QtCore
from PyQt6.QtWidgets import QApplication

from find_bird_util import make_new_dir, log_message, detect_and_draw_birds, get_model, move_originals


class Worker(QtCore.QThread):
    updateProgress = QtCore.pyqtSignal(int)

    def __init__(self, dir_pth, parent=None):
        super(Worker, self).__init__(parent)
        self.dir_pth = dir_pth

    def run(self):
        output_dir = make_new_dir(self.dir_pth, "Boxed")
        super_picky_dir = make_new_dir(self.dir_pth, "Super_Picky")
        bird_detected_dir = make_new_dir(self.dir_pth, "Contains_Birds")
        no_birds_dir = make_new_dir(self.dir_pth, "No_Birds")

        resized_dir = os.path.join(self.dir_pth, "Resized")
        if not os.path.exists(resized_dir):
            log_message("ERROR: 'Resized' folder not found.", self.dir_pth)
            return None

        files = os.listdir(resized_dir)
        total_files = len(files)

        for i, filename in enumerate(files):
            # Emit the progress update signal
            self.updateProgress.emit(int((i / total_files) * 100))

            log_message("=" * 30, self.dir_pth)
            log_message(f"Processing file: {filename}", self.dir_pth)
            file_prefix, file_ext = os.path.splitext(filename)

            filepath = os.path.join(resized_dir, filename)
            output_pth = os.path.join(output_dir, filename)

            # runs model and draws a box on the resized image
            result = detect_and_draw_birds(filepath, get_model(), output_pth)
            if result is None:
                continue
            detected, dominant, centered, sharp = result[0], result[1], result[2], result[3]

            log_message(f"RESULTS-----detected: {detected}, dominant: {dominant}, centered: {centered},"
                        f" sharp: {sharp}", self.dir_pth)

            save_to_pth = self.dir_pth
            if detected:
                if dominant and sharp:
                    save_to_pth = super_picky_dir
                else:
                    save_to_pth = bird_detected_dir
            else:
                save_to_pth = no_birds_dir

            move_originals(file_prefix, self.dir_pth, save_to_pth)

        # Emit the progress update signal
        self.updateProgress.emit(int((i+1 / total_files) * 100))