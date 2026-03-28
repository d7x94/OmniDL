# ui/tabs/settings/__init__.py
# Re-export panels for convenient import by settings_tab.py
from ui.tabs.settings.general_panel    import GeneralPanel
from ui.tabs.settings.network_panel    import NetworkPanel
from ui.tabs.settings.tools_panel      import ToolsPanel
from ui.tabs.settings.taildrop_panel   import TaildropPanel
from ui.tabs.settings.remote_api_panel import RemoteApiPanel

__all__ = [
    "GeneralPanel",
    "NetworkPanel",
    "ToolsPanel",
    "TaildropPanel",
    "RemoteApiPanel",
]
