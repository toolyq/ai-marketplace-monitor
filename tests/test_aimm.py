"""Tests for `ai_marketplace_monitor` module."""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from diskcache import Cache  # type: ignore

from ai_marketplace_monitor.ai import AIResponse  # type: ignore
from ai_marketplace_monitor.facebook import FacebookItemConfig
from ai_marketplace_monitor.listing import Listing
from ai_marketplace_monitor.monitor import MarketplaceMonitor
from ai_marketplace_monitor.notification import NotificationStatus
from ai_marketplace_monitor.user import User


def test_version(version: str) -> None:
    """Sample pytest test function with the pytest fixture as an argument."""
    assert version and version[0].isdigit()


def test_listing_cache(temp_cache: Cache, listing: Listing) -> None:
    listing.to_cache(post_url=listing.post_url, local_cache=temp_cache)
    #
    new_listing = Listing.from_cache(listing.post_url, local_cache=temp_cache)

    for attr in (
        "marketplace",
        "name",
        "id",
        "title",
        "image",
        "price",
        "location",
        "seller",
        "condition",
        "description",
    ):
        assert getattr(listing, attr) == getattr(new_listing, attr)


def test_notification_cache(temp_cache: Cache, user: User, listing: Listing) -> None:
    assert (
        user.notification_status(listing, local_cache=temp_cache)
        == NotificationStatus.NOT_NOTIFIED
    )
    assert user.time_since_notification(listing, local_cache=temp_cache) == -1
    user.to_cache(listing, local_cache=temp_cache)

    assert user.notified_key(listing) in temp_cache
    assert user.notification_status(listing, local_cache=temp_cache) == NotificationStatus.NOTIFIED
    assert user.time_since_notification(listing, local_cache=temp_cache) >= 0

    #
    user.config.remind = 1

    time.sleep(2)

    assert user.notification_status(listing, local_cache=temp_cache) == NotificationStatus.EXPIRED

    # change listing
    listing.price = "$30000"
    assert (
        user.notification_status(listing, local_cache=temp_cache)
        == NotificationStatus.LISTING_CHANGED
    )


def test_notify_all(
    user: User, item_config: FacebookItemConfig, listing: Listing, ai_response: AIResponse
) -> None:
    user.notify([listing], [ai_response], item_config)


def test_search_item_notifies_even_when_no_results(
    item_config: FacebookItemConfig, marketplace_config, user_config, monkeypatch
) -> None:
    monitor = MarketplaceMonitor.__new__(MarketplaceMonitor)
    monitor.config = SimpleNamespace(user={"user1": user_config})
    monitor.logger = None

    marketplace = MagicMock()
    marketplace.search.return_value = iter(())

    notify_calls = []

    def fake_notify(
        self,
        listings,
        ratings,
        item_cfg,
        local_cache=None,
        force=False,
        marketplace_name=None,
        send_empty=False,
    ) -> None:
        notify_calls.append(
            {
                "listings": listings,
                "ratings": ratings,
                "item_name": item_cfg.name,
                "marketplace_name": marketplace_name,
                "send_empty": send_empty,
            }
        )

    monkeypatch.setattr(User, "notify", fake_notify)
    monkeypatch.setattr("ai_marketplace_monitor.monitor.time.sleep", lambda _: None)

    monitor.search_item(marketplace_config, marketplace, item_config)

    assert notify_calls == [
        {
            "listings": [],
            "ratings": [],
            "item_name": item_config.name,
            "marketplace_name": marketplace_config.name,
            "send_empty": True,
        }
    ]
