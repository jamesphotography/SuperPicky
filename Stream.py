import sys
from PyQt6.QtWidgets import QApplication, QTextBrowser
from PyQt6 import QtCore

class Stream(QtCore.QObject):
    newText = QtCore.pyqtSignal(str)

    def write(self, text):
        self.newText.emit(str(text))

    def flush(self):
        pass
