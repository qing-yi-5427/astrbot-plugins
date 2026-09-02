from core.config import PluginSettings


def test_force_text_message_is_enabled_by_default():
    assert PluginSettings.from_mapping({}).output.force_text_message is True


def test_force_text_message_can_follow_global_setting():
    settings = PluginSettings.from_mapping({"output": {"force_text_message": False}})
    assert settings.output.force_text_message is False
