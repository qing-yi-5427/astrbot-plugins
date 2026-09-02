from core.config import PluginSettings


def test_force_text_message_is_enabled_by_default():
    assert PluginSettings.from_mapping({}).output.force_text_message is True


def test_force_text_message_can_follow_global_setting():
    settings = PluginSettings.from_mapping({"output": {"force_text_message": False}})
    assert settings.output.force_text_message is False


def test_ascii2d_fallback_defaults_and_cookie_config():
    defaults = PluginSettings.from_mapping({})
    assert defaults.search.enable_ascii2d is True
    assert defaults.search.ascii2d_session_id == ""

    configured = PluginSettings.from_mapping(
        {
            "search": {
                "enable_ascii2d": False,
                "ascii2d_session_id": " session ",
                "ascii2d_cf_clearance": " clearance ",
            },
            "network": {
                "ascii2d_proxy_url": " http://proxy:8080 ",
                "ascii2d_user_agent": " Browser/1 ",
            },
        }
    )
    assert configured.search.enable_ascii2d is False
    assert configured.search.ascii2d_session_id == "session"
    assert configured.search.ascii2d_cf_clearance == "clearance"
    assert configured.network.ascii2d_proxy_url == "http://proxy:8080"
    assert configured.network.ascii2d_user_agent == "Browser/1"
