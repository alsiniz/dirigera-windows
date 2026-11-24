"""Icons for the notification area and the controls window."""

from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStyle

APP_ICON = Path(__file__).resolve().parent / "assets" / "dirigera.svg"

_STYLE_PIXMAPS = {
    "Computer": QStyle.StandardPixmap.SP_ComputerIcon,
    "Desktop": QStyle.StandardPixmap.SP_DesktopIcon,
    "Help": QStyle.StandardPixmap.SP_DialogHelpButton,
    "Information": QStyle.StandardPixmap.SP_MessageBoxInformation,
    "Network": QStyle.StandardPixmap.SP_DriveNetIcon,
}


def system_icons() -> dict[str, QIcon]:
    style = QApplication.style()
    return {
        "DIRIGERA": QIcon(str(APP_ICON)),
        **{name: style.standardIcon(pixmap) for name, pixmap in _STYLE_PIXMAPS.items()},
    }
