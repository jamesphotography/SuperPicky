import os

from PyQt6 import QtCore

from find_bird_util import make_new_dir, log_message, detect_and_draw_birds, get_model, move_originals


class Worker(QtCore.QThread):
    updateProgress = QtCore.pyqtSignal(int)
    finishedProcessing = QtCore.pyqtSignal()  # Signal to indicate completion

    def __init__(self, dir_pth, processed_files, ui_settings, parent=None):
        super(Worker, self).__init__(parent)
        self.dir_pth = dir_pth
        self.processed_files = processed_files
        self.ui_settings = ui_settings

    def run(self):


        processed_files = set()
        output_dir = make_new_dir(self.dir_pth, "Boxed")
        super_picky_dir = make_new_dir(self.dir_pth, "Super_Picky")
        bird_detected_dir = make_new_dir(self.dir_pth, "Contains_Birds")
        no_birds_dir = make_new_dir(self.dir_pth, "No_Birds")
        process_bar = 0

        resized_dir = os.path.join(self.dir_pth, "Resized")
        if not os.path.exists(resized_dir):
            log_message("ERROR: 'Resized' folder not found.", self.dir_pth)
            return None

        files = os.listdir(resized_dir)
        total_files = len(files)

        for i, filename in enumerate(files):
            log_message(f"Attempting to process {filename}", self.dir_pth)
            if filename in processed_files:
                log_message(f"Skipping {filename}, already processed", self.dir_pth)
                continue
            if i < process_bar:
                log_message(f"Skipping {filename} due to i: {i} being less than bar{process_bar}", self.dir_pth)
                continue

            process_bar += 1
            self.processed_files.add(filename)
            # Emit the progress update signal
            self.updateProgress.emit(int((process_bar / total_files) * 100))

            log_message("=" * 50, self.dir_pth)
            log_message(f"Processing file: {filename}", self.dir_pth)
            file_prefix, file_ext = os.path.splitext(filename)


            filepath = os.path.join(resized_dir, filename)
            output_pth = os.path.join(output_dir, filename)

            if not os.path.exists(filepath):
                log_message(f"ERROR: attempting to process file that does not exist {filename}", self.dir_pth)
                continue

                # runs model and draws a box on the resized image
            result = detect_and_draw_birds(filepath, get_model(), output_pth, self.ui_settings)
            if result is None:
                log_message(f"ERROR: Input file [{filepath}] not an image of jpg format", self.dir_pth)
                continue
            detected, dominant, centered, sharp = result[0], result[1], result[2], result[3]

            log_message(f"RESULTS: [detected = {detected}, dominant = {dominant}, centered = {centered},"
                        f" sharp = {sharp}]", self.dir_pth)

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
        self.updateProgress.emit(100)

        log_message(f"Process Completed, files processed: {total_files}", self.dir_pth)
        self.finishedProcessing.emit()  # Emit signal indicating completion

