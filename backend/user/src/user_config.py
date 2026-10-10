"""
Functionality:
- read and write user config backed by the UserConfig model
- encapsulate persistence of user properties
"""

from typing import TypedDict

from django.db import transaction


class UserConfigType(TypedDict, total=False):
    """describes the user configuration"""

    stylesheet: str
    page_size: int
    sort_by: str
    sort_order: str
    view_style_home: str
    view_style_channel: str
    view_style_downloads: str
    view_style_playlist: str
    vid_type_filter: str | None
    grid_items: int
    hide_watched: bool | None
    hide_watched_channel: bool | None
    hide_watched_playlist: bool | None
    file_size_unit: str
    show_ignored_only: bool
    show_subed_only: bool | None
    show_subed_only_playlists: bool | None
    show_help_text: bool


class UserConfig:
    """
    Handle settings for an individual user
    items are expected to be validated in the serializer
    """

    _DEFAULT_USER_SETTINGS = UserConfigType(
        stylesheet="dark.css",
        page_size=25,
        sort_by="published",
        sort_order="desc",
        view_style_home="grid",
        view_style_channel="list",
        view_style_downloads="list",
        view_style_playlist="grid",
        vid_type_filter=None,
        grid_items=3,
        hide_watched=False,
        hide_watched_channel=None,
        hide_watched_playlist=None,
        file_size_unit="binary",
        show_ignored_only=False,
        show_subed_only=None,
        show_subed_only_playlists=None,
        show_help_text=True,
    )

    def __init__(self, user_id: str):
        self._user_id: str = user_id
        self._config: UserConfigType = self.get_config()

    def get_value(self, key: str):
        """Get the given key from the users configuration
        Throws a KeyError if the requested Key is not a permitted value"""
        if key not in self._DEFAULT_USER_SETTINGS:
            raise KeyError(f"Unable to read config for unknown key '{key}'")

        return self._config.get(key)

    def set_value(self, key: str, value: str | bool | int):
        """Set or replace a configuration value for the user"""
        self.update_config({key: value})

    def get_config(self) -> UserConfigType:
        """get config from the database or load the application defaults"""
        if not self._user_id:
            raise ValueError("no user_id passed")

        # pylint: disable=import-outside-toplevel
        from user.models import UserConfig as UserConfigModel

        stored, created = UserConfigModel.objects.get_or_create(
            user_id=self._user_id,
            defaults={"config": dict(self._DEFAULT_USER_SETTINGS)},
        )
        if created:
            print(f"set default config for user {self._user_id}")
            return UserConfigType(**stored.config)

        return self.sync_new_defaults(stored.config)

    def update_config(self, to_update: dict) -> None:
        """update config object"""
        # pylint: disable=import-outside-toplevel
        from user.models import UserConfig as UserConfigModel

        with transaction.atomic():
            (
                stored,
                _,
            ) = UserConfigModel.objects.select_for_update().get_or_create(
                user_id=self._user_id,
                defaults={"config": dict(self._DEFAULT_USER_SETTINGS)},
            )
            stored.config.update(to_update)
            stored.save(update_fields=["config", "updated"])

        self._config.update(to_update)  # type: ignore
        for key, value in to_update.items():
            print(f"User {self._user_id} value '{key}' change: to {value}")

    def sync_new_defaults(self, config):
        """add defaults for keys this user has no value for yet"""
        missing = {
            key: value
            for key, value in self._DEFAULT_USER_SETTINGS.items()
            if key not in config
        }
        config.update(missing)
        if missing:
            self._config = config
            self.update_config(missing)

        return config
