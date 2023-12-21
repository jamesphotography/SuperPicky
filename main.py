import sys
from PyQt6.QtWidgets import QApplication

from MainWindow import MainWindow
from main_ui import Ui_Dialog



# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    # Initialize the application

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()

    # Run the application
    sys.exit(app.exec())

# See PyCharm help at https://www.jetbrains.com/help/pycharm/
