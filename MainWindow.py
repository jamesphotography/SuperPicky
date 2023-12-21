import sys
from PyQt6.QtWidgets import QMainWindow, QLabel, QVBoxLayout, QWidget

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # Set window title and size
        self.setWindowTitle("SuperPicky")
        self.setGeometry(100, 100, 1100, 800)

        # Create a layout and a label widget
        layout = QVBoxLayout()
        label = QLabel("Hello, PyQt6!")

        # Add the label to the layout
        layout.addWidget(label)

        # Create a central widget and set the layout
        central_widget = QWidget()
        central_widget.setLayout(layout)

        # Set the central widget of the window
        self.setCentralWidget(central_widget)