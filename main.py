import sys
import time

from PyQt6.QtWidgets import QApplication

from MainWindow import MainWindow
# main.py
from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtGui import QPixmap



# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    # Initialize the application
    app = QApplication(sys.argv)
    splash_img = QPixmap("/Users/jameszhenyu/PycharmProjects/SuperPickyV0.02/DALL·E 2023-12-22 17.20.36 - a detailed and colorful portrait of a rainbow lorikeet with a magnifying glass focused on the bird's eye, with a smaller and more detailed hand partia.png")
    splash = QSplashScreen(splash_img)
    splash.show()

    window = MainWindow()
    window.show()
    # Run the application
    sys.exit(app.exec())

# See PyCharm help at https://www.jetbrains.com/help/pycharm/
