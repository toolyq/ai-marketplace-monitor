import sys
import time
from logging import Logger
from pathlib import Path
from typing import ClassVar, List

import humanize
import inflect
import rich
import schedule  # type: ignore
from playwright.sync_api import Browser, Playwright, sync_playwright
from rich.pretty import pretty_repr
from rich.prompt import Prompt

from .ai import AIBackend, AIResponse
from .config import Config, supported_ai_backends, supported_marketplaces
from .listing import Listing
from .marketplace import Marketplace, SearchPhraseComplete, TItemConfig, TMarketplaceConfig
from .notification import NotificationStatus
from .user import User
from .utils import (
    CounterItem,
    KeyboardMonitor,
    SleepStatus,
    Translator,
    aimm_event,
    amm_home,
    cache,
    calculate_file_hash,
    counter,
    doze,
    hilight,
)


class MarketplaceMonitor:
    active_marketplaces: ClassVar = {}

    def __init__(
        self: "MarketplaceMonitor",
        config_files: List[Path] | None,
        headless: bool | None,
        logger: Logger | None,
    ) -> None:
        package_config = (Path(__file__).parent / "config.toml").resolve()
        if not package_config.exists():
            raise FileNotFoundError(f"Config file {package_config} not found.")
        # Strict config source: only use the package config file.
        # Ignore user-home and CLI-provided config files.
        self.config_files = [package_config]
        #
        self.config: Config | None = None
        self.config_hash: str | None = None
        self.headless = headless
        # When True, start_monitor blocks until every enabled marketplace
        # has a username + password in the config. The web UI sets this
        # so Playwright doesn't race the web UI for Facebook credentials.
        self.defer_login_until_credentials: bool = False
        self.ai_agents: List[AIBackend] = []
        self.keyboard_monitor: KeyboardMonitor | None = None
        self.playwright: Playwright = sync_playwright().start()
        self.browser: Browser | None = None
        self.logger = logger

    def load_config_file(self: "MarketplaceMonitor") -> Config:
        """Load the configuration file."""
        last_invalid_hash = None
        while True:
            new_file_hash = calculate_file_hash(self.config_files)
            config_changed = self.config_hash is None or new_file_hash != self.config_hash
            if not config_changed:
                assert self.config is not None
                return self.config
            try:
                # if the config file is ok, break
                assert self.logger is not None
                self.config = Config(self.config_files, self.logger)
                self.config_hash = new_file_hash
                # self.logger.debug(self.config)
                assert self.config is not None
                return self.config
            except KeyboardInterrupt:
                raise
            except Exception as e:
                if last_invalid_hash != new_file_hash:
                    last_invalid_hash = new_file_hash
                    if self.logger:
                        self.logger.error(
                            f"""{hilight("[Config]", "fail")} Error parsing:\n\n{hilight(str(e), "fail")}\n\nPlease fix the configuration and I will try again as soon as you are done."""
                        )
                doze(60, self.config_files, self.keyboard_monitor)
                continue

    def _launch_browser(self: "MarketplaceMonitor") -> Browser:
        """Launch a browser.

        Strict CDP mode: connect to an existing Chromium instance over CDP.
        No fallback to launching local Playwright browsers.
        """
        assert self.config is not None
        cdp_url = self.config.monitor.cdp_url
        if not cdp_url:
            raise RuntimeError(
                "Strict CDP mode requires monitor.cdp_url in config. "
                "Example: cdp_url = 'http://127.0.0.1:9222'"
            )

        try:
            kwargs = {}
            if self.config.monitor.cdp_timeout is not None:
                kwargs["timeout"] = self.config.monitor.cdp_timeout
            if self.logger:
                self.logger.info(
                    f"""{hilight("[Browser]", "info")} Connecting to Chromium over CDP: {hilight(cdp_url)}""",
                    extra=aimm_event("browser_connect", mode="cdp", endpoint=cdp_url),
                )
            browser = self.playwright.chromium.connect_over_cdp(cdp_url, **kwargs)
            if self.logger:
                self.logger.info(
                    f"""{hilight("[Browser]", "succ")} Connected to Chromium via CDP.""",
                    extra=aimm_event(
                        "browser_ready", engine="chromium", mode="cdp", endpoint=cdp_url
                    ),
                )
            return browser
        except Exception as e:
            raise RuntimeError(
                "Failed to connect to Chromium via CDP. "
                "Start Chrome/Chromium with --remote-debugging-port and verify monitor.cdp_url. "
                f"Endpoint: {cdp_url}. Error: {e}"
            ) from e

    def load_ai_agents(self: "MarketplaceMonitor") -> None:
        """Load the AI agent."""
        assert self.config is not None
        for ai_config in (self.config.ai or {}).values():
            if ai_config.enabled is False:
                continue
            if (
                ai_config.provider is not None
                and ai_config.provider.lower() in supported_ai_backends
            ):
                ai_class = supported_ai_backends[ai_config.provider.lower()]
            elif ai_config.name.lower() in supported_ai_backends:
                ai_class = supported_ai_backends[ai_config.name.lower()]
            else:
                if self.logger:
                    self.logger.error(
                        f"""{hilight("[Config]", "fail")} Cannot determine an AI service provider from service name or provider."""
                    )
                continue

            try:
                self.ai_agents.append(ai_class(config=ai_config, logger=self.logger))
                # self.ai_agents[-1].connect()
                # self.logger.info(
                #     f"""{hilight("[AI]", "succ")} Connected to {hilight(ai_config.name)}"""
                # )
            except KeyboardInterrupt:
                raise
            except Exception as e:
                if self.logger:
                    self.logger.error(
                        f"""{hilight("[AI]", "fail")} Failed to connect to {hilight(ai_config.name, "fail")}: {e}"""
                    )
                continue

    def search_item(
        self: "MarketplaceMonitor",
        marketplace_config: TMarketplaceConfig,
        marketplace: Marketplace,
        item_config: TItemConfig,
    ) -> None:
        """Search for an item on the marketplace."""
        new_listings: List[Listing] = []
        listing_ratings = []
        phrase_new_count = 0
        # users to notify is determined from item, then marketplace, then all users
        assert self.config is not None
        users_to_notify = (
            item_config.notify or marketplace_config.notify or list(self.config.user.keys())
        )
        for listing in marketplace.search(item_config):
            # handle per-search-phrase completion sentinel
            if isinstance(listing, SearchPhraseComplete):
                for user in users_to_notify:
                    User(self.config.user[user], logger=self.logger).notify(
                        [],
                        [],
                        item_config,
                        marketplace_name=marketplace_config.name,
                        send_summary=True,
                        summary_new_count=phrase_new_count,
                    )
                phrase_new_count = 0
                continue
            # duplicated ID should not happen, but sellers could repost the same listing,
            # potentially under different seller names
            if listing.id in [x.id for x in new_listings] or listing.content in [
                x.content for x in new_listings
            ]:
                if self.logger:
                    self.logger.debug(f"Found duplicated result for {listing}")
                continue
            # if everyone has been notified
            if all(
                User(self.config.user[user], self.logger).notification_status(listing)
                == NotificationStatus.NOTIFIED
                for user in users_to_notify
            ):
                if self.logger:
                    self.logger.info(
                        f"""{hilight("[Skip]", "info")} Already sent notification for item {hilight(listing.title)}, skipping.""",
                        extra=aimm_event(
                            "listing_skip",
                            reason="already_notified",
                            listing_id=listing.id,
                            title=listing.title,
                            item=item_config.name,
                        ),
                    )
                continue
            # for x in self.find_new_items(found_items)
            res = self.evaluate_by_ai(
                listing, item_config=item_config, marketplace_config=marketplace_config
            )
            if self.logger:
                if res.comment == AIResponse.NOT_EVALUATED:
                    if res.name:
                        self.logger.info(
                            f"""{hilight("[AI]", res.style)} {res.name or "AI"} did not evaluate {hilight(listing.title)}."""
                        )
                    else:
                        self.logger.info(
                            f"""{hilight("[AI]", res.style)} No AI available to evaluate {hilight(listing.title)}."""
                        )
                else:
                    self.logger.info(
                        f"""{hilight("[AI]", res.style)} {res.name or "AI"} concludes {hilight(f"{res.conclusion} ({res.score}): {res.comment}", res.style)} for listing {hilight(listing.title)}.""",
                        extra=aimm_event(
                            "ai_eval",
                            listing_id=listing.id,
                            title=listing.title,
                            url=getattr(listing, "post_url", None)
                            or getattr(listing, "url", None),
                            price=getattr(listing, "price", None),
                            score=res.score,
                            conclusion=res.conclusion,
                            comment=res.comment,
                            ai_name=res.name,
                            item=item_config.name,
                        ),
                    )
            if item_config.rating:
                acceptable_rating = item_config.rating[
                    0 if item_config.searched_count == 0 else -1
                ]
            elif marketplace_config.rating:
                acceptable_rating = marketplace_config.rating[
                    0 if item_config.searched_count == 0 else -1
                ]
            else:
                acceptable_rating = 3

            if res.score < acceptable_rating:
                if self.logger:
                    self.logger.info(
                        f"""{hilight("[Skip]", "fail")} Rating {hilight(f"{res.conclusion} ({res.score})")} for {listing.title} is below threshold {acceptable_rating}.""",
                        extra=aimm_event(
                            "listing_skip",
                            reason="below_threshold",
                            listing_id=listing.id,
                            title=listing.title,
                            item=item_config.name,
                            score=res.score,
                            threshold=acceptable_rating,
                        ),
                    )
                counter.increment(CounterItem.EXCLUDED_LISTING, item_config.name)
                continue
            new_listings.append(listing)
            listing_ratings.append(res)
            phrase_new_count += 1
            for user in users_to_notify:
                User(self.config.user[user], logger=self.logger).notify(
                    [listing],
                    [res],
                    item_config,
                    marketplace_name=marketplace_config.name,
                )

        p = inflect.engine()
        if self.logger:
            self.logger.info(
                f"""{hilight("[Search]", "succ" if len(new_listings) > 0 else "fail")} {hilight(str(len(new_listings)))} new {p.plural_noun("listing", len(new_listings))} for {item_config.name} {p.plural_verb("is", len(new_listings))} found.""",
                extra=aimm_event(
                    "search_summary",
                    item=item_config.name,
                    marketplace=marketplace_config.name,
                    new_count=len(new_listings),
                ),
            )
        if new_listings:
            counter.increment(
                CounterItem.NEW_VALIDATED_LISTING, item_config.name, len(new_listings)
            )
        for user in users_to_notify:
            User(self.config.user[user], logger=self.logger).notify(
                [],
                [],
                item_config,
                marketplace_name=marketplace_config.name,
                send_summary=True,
                summary_new_count=len(new_listings),
            )
        time.sleep(5)

    def _select_translator(
        self: "MarketplaceMonitor", language: str | None = None
    ) -> Translator | None:
        """Select the language for the marketplace."""
        # self.config.translator.get(marketplace_config.language, None)
        assert self.config is not None
        if not language:
            return None
        if language in self.config.translator:
            return self.config.translator[language]
        # if there is no exact match, we are going to match the language code
        # e.g. 'en' to 'en_US'
        if "_" in language:
            # if a more general languge exists?
            if language.split("_")[0] in self.config.translator:
                translator = self.config.translator[language.split("_")[0]]
                if self.logger:
                    self.logger.info(
                        f"""{hilight("[Translator]", "info")} Using language {language.split("_")[0]} (locale {translator.locale}) for {language} translation."""
                    )
                return translator
            # if not, we are going to match the language code
            # e.g. 'en' to 'en_US'
            for name, translator in self.config.translator.items():
                if name.startswith(language.split("_")[0] + "_"):
                    if self.logger:
                        self.logger.info(
                            f"""{hilight("[Translator]", "info")} Using language {name} (locale {translator.locale}) for {language} translation."""
                        )
                    return translator
        # if there is no match, we are going to match the language code
        # e.g. 'en' to 'en_US'
        for name, translator in self.config.translator.items():
            if name.startswith(language + "_"):
                if self.logger:
                    self.logger.info(
                        f"""{hilight("[Translator]", "info")} Using language {name} (locale {translator.locale}) for {language} translation."""
                    )
                return translator
        raise RuntimeError(f"Cannot find translator for language {language}.")

    def schedule_jobs(self: "MarketplaceMonitor") -> None:
        """Schedule jobs to run periodically."""
        # we reload the config file each time when a scan action is completed
        # this allows users to add/remove products dynamically.
        self.load_config_file()
        self.load_ai_agents()

        assert self.config is not None
        for marketplace_config in self.config.marketplace.values():
            if marketplace_config.enabled is False:
                continue
            marketplace_class = supported_marketplaces[marketplace_config.name]
            if marketplace_config.name in self.active_marketplaces:
                marketplace = self.active_marketplaces[marketplace_config.name]
            else:
                marketplace = marketplace_class(
                    marketplace_config.name, self.browser, self.keyboard_monitor, self.logger
                )
                self.active_marketplaces[marketplace_config.name] = marketplace

            # Configure might have been changed
            marketplace.configure(
                marketplace_config,
                translator=self._select_translator(marketplace_config.language),
            )

            for item_config in self.config.item.values():
                if item_config.enabled is False:
                    continue

                if (
                    item_config.marketplace is None
                    or item_config.marketplace == marketplace_config.name
                ):
                    # wait for some time before next search
                    # interval (in minutes) can be defined both for the marketplace
                    # if there is any configuration file change, stop sleeping and search again
                    scheduled = None
                    start_at_list = item_config.start_at or marketplace_config.start_at
                    if start_at_list is not None and start_at_list:
                        for start_at in start_at_list:
                            if start_at.startswith("*:*:"):
                                # '*:*:12' to ':12'
                                if self.logger:
                                    self.logger.info(
                                        f"""{hilight("[Schedule]", "info")} Scheduling to search for {item_config.name} every minute at {start_at[3:]}s"""
                                    )
                                scheduled = schedule.every().minute.at(start_at[3:])
                            elif start_at.startswith("*:"):
                                # '*:12:12' or  '*:12'
                                if self.logger:
                                    self.logger.info(
                                        f"""{hilight("[Schedule]", "info")} Scheduling to search for {item_config.name} every hour at {start_at[1:]}m"""
                                    )
                                scheduled = schedule.every().hour.at(
                                    start_at[1:] if start_at.count(":") == 1 else start_at[2:]
                                )
                            else:
                                # '12:12:12' or '12:12'
                                if self.logger:
                                    self.logger.info(
                                        f"""{hilight("[Schedule]", "ss")} Scheduling to search for {item_config.name} every day at {start_at}"""
                                    )
                                scheduled = schedule.every().day.at(start_at)
                    else:
                        search_interval = max(
                            item_config.search_interval
                            or marketplace_config.search_interval
                            or 30 * 60,
                            1,
                        )
                        max_search_interval = max(
                            item_config.max_search_interval
                            or marketplace_config.max_search_interval
                            or 60 * 60,
                            search_interval,
                        )
                        if self.logger:
                            self.logger.info(
                                f"""{hilight("[Schedule]", "info")} Scheduling to search for {item_config.name} every {humanize.naturaldelta(search_interval)} {"" if search_interval == max_search_interval else f"to {humanize.naturaldelta(max_search_interval)}"}"""
                            )
                        scheduled = schedule.every(search_interval).to(max_search_interval).seconds
                    if scheduled is None:
                        raise ValueError(
                            f"Cannot determine a schedule for {item_config.name} from configuration file."
                        )
                    scheduled.do(
                        self.search_item,
                        marketplace_config,
                        marketplace,
                        item_config,
                    ).tag(item_config.name)

    def handle_pause(self: "MarketplaceMonitor") -> None:
        """Handle interruption signal."""
        if self.keyboard_monitor is None or not self.keyboard_monitor.is_paused():
            return

        rich.print(counter)
        if not self.keyboard_monitor.confirm():
            return

        # now we should go to an interactive session
        while True:
            while True:
                url = (
                    Prompt.ask(
                        f"""\nEnter an {hilight("ID")} or a {hilight("URL")} to check, or {hilight("exit")}."""
                    )
                    .strip("\x1b")
                    .strip()
                )

                if not url.isnumeric() and not url.startswith("https://"):
                    if url.endswith("exit"):
                        url = "exit"
                        break
                    if url:
                        print(f'Invalid input "{url}". Please try again.')
                else:
                    break

            if url == "exit":
                break

            try:
                self.check_items([url], for_item=None)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                if self.logger:
                    self.logger.debug(f"Failed to check item {url}: {e}")

    def _has_marketplace_credentials(self: "MarketplaceMonitor") -> bool:
        """True if every enabled marketplace has a username and password.

        Used to defer launching the Playwright browser until the user has
        provided credentials (typically via the web UI), to avoid the
        confusing state of two places asking for Facebook login at once.
        """
        assert self.config is not None
        for mp in self.config.marketplace.values():
            if getattr(mp, "enabled", True) is False:
                continue
            if not getattr(mp, "username", None) or not getattr(mp, "password", None):
                return False
        return True

    def _wait_for_marketplace_credentials(self: "MarketplaceMonitor") -> None:
        """Block until config has marketplace credentials.

        Reloads the config whenever the file changes on disk.
        No-op if credentials are already present.
        """
        assert self.config is not None
        while not self._has_marketplace_credentials():
            if self.logger:
                self.logger.info(
                    f"""{hilight("[Login]", "info")} Waiting for Facebook credentials. Sign in via the web UI or add username/password under [marketplace.facebook] in your config. The Playwright browser will launch once credentials are available.""",
                    extra=aimm_event("credentials_wait", status="waiting"),
                )
            # doze wakes up on file change OR keyboard interrupt OR timeout.
            doze(300, self.config_files, self.keyboard_monitor)
            # File may have changed — reload the config (non-fatal on parse error).
            try:
                self.load_config_file()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                if self.logger:
                    self.logger.debug(f"Config reload failed during credential wait: {e}")
                continue
        if self.logger:
            self.logger.info(
                f"""{hilight("[Login]", "succ")} Facebook credentials found — launching browser.""",
                extra=aimm_event("credentials_wait", status="found"),
            )

    def start_monitor(self: "MarketplaceMonitor") -> None:
        """Run all configured search tasks once, then exit."""
        # start a browser with playwright, cannot use with statement since the jobs will be
        # executed outside of the scope by schedule job runner
        self.keyboard_monitor = KeyboardMonitor()
        self.keyboard_monitor.start()

        # Open a new browser page.
        self.load_config_file()
        assert self.config is not None
        # If requested (by the web UI), defer browser launch until
        # marketplace credentials are set. Without this, Playwright
        # navigates to the Facebook login page and waits for manual
        # input even though the user has a web UI open that's also
        # asking for those same credentials.
        if self.defer_login_until_credentials:
            self._wait_for_marketplace_credentials()
        self.browser = self._launch_browser()
        assert self.browser is not None
        self.handle_pause()
        self.schedule_jobs()
        if not schedule.get_jobs():
            raise RuntimeError("No search job is defined. Please add search items to your config file.")

        for job in schedule.get_jobs():
            job.run()
            self.handle_pause()

        if self.logger:
            self.logger.info(f"""{hilight("[Schedule]", "succ")} All search tasks completed. Exiting.""")
        schedule.clear()
        self.stop_monitor()
        sys.exit(0)

    def stop_monitor(self: "MarketplaceMonitor") -> None:
        """Stop the monitor."""
        for marketplace in self.active_marketplaces.values():
            marketplace.stop()
        self.playwright.stop()
        if self.keyboard_monitor:
            self.keyboard_monitor.stop()
        cache.close()

    def check_items(
        self: "MarketplaceMonitor", items: List[str] | None = None, for_item: str | None = None
    ) -> None:
        """Main function to monitor the marketplace."""
        # we reload the config file each time when a scan action is completed
        # this allows users to add/remove products dynamically.
        self.load_config_file()

        if for_item is not None:
            assert self.config is not None
            if for_item not in self.config.item:
                raise ValueError(
                    f"Item {for_item} not found in config, available items are {', '.join(self.config.item.keys())}."
                )

        self.load_ai_agents()

        post_urls = []
        for post_url in items or []:
            if post_url.isnumeric():
                post_url = f"https://www.facebook.com/marketplace/item/{post_url}/"

            if not post_url.startswith("https://www.facebook.com/marketplace/item"):
                raise ValueError(f"URL {post_url} is not a valid Facebook Marketplace URL.")
            post_urls.append(post_url)

        if not post_urls:
            raise ValueError("No URLs to check.")

        # Open a new browser page.
        for post_url in post_urls or []:
            # check if item in config
            assert self.config is not None

            # which marketplace to check it?
            for marketplace_config in self.config.marketplace.values():
                if marketplace_config.enabled is False:
                    continue
                marketplace_class = supported_marketplaces[marketplace_config.name]
                if marketplace_config.name in self.active_marketplaces:
                    marketplace = self.active_marketplaces[marketplace_config.name]
                else:
                    marketplace = marketplace_class(
                        marketplace_config.name, None, None, self.logger
                    )
                    self.active_marketplaces[marketplace_config.name] = marketplace

                # Configure might have been changed
                marketplace.configure(
                    marketplace_config,
                    translator=self._select_translator(marketplace_config.language),
                )

                # do we need a browser?
                if Listing.from_cache(post_url) is None:
                    if self.browser is None:
                        if self.logger:
                            self.logger.info(
                                f"""{hilight("[Search]", "info")} Starting a browser because the item was not checked before."""
                            )
                        self.browser = self._launch_browser()
                        marketplace.set_browser(self.browser)

                # ignore enabled
                if for_item is None:
                    # get by asking user
                    name = None
                    item_names = list(self.config.item.keys())
                    if len(item_names) > 1:
                        name = Prompt.ask(
                            f"""Enter name of {hilight("search item")}""", choices=item_names
                        )
                    item_config = self.config.item[name or item_names[0]]
                else:
                    item_config = self.config.item[for_item]

                # do not search, get the item details directly
                listing_result = marketplace.get_listing_details(post_url, item_config)

                # get_listing_details returns a tuple (Listing, bool) - unpack it properly
                if isinstance(listing_result, tuple) and len(listing_result) == 2:
                    listing, from_cache = listing_result
                else:
                    # Fallback - treat as direct listing (shouldn't happen but defensive)
                    listing = listing_result

                if self.logger:
                    self.logger.info(
                        f"""{hilight("[Retrieve]", "succ")} Details of the item is found: {pretty_repr(listing)}"""
                    )

                if self.logger:
                    self.logger.info(
                        f"""{hilight("[Search]", "succ")} Checking {post_url} for item {item_config.name} with configuration {pretty_repr(item_config)}"""
                    )
                marketplace.check_listing(listing, item_config)
                rating = self.evaluate_by_ai(
                    listing, item_config=item_config, marketplace_config=marketplace_config
                )
                if self.logger:
                    if rating.comment == AIResponse.NOT_EVALUATED:
                        if rating.name:
                            self.logger.info(
                                f"""{hilight("[AI]", rating.style)} {rating.name or "AI"} did not evaluate {hilight(listing.title)}."""
                            )
                        else:
                            self.logger.info(
                                f"""{hilight("[AI]", rating.style)} No AI available to evaluate {hilight(listing.title)}."""
                            )
                    else:
                        self.logger.info(
                            f"""{hilight("[AI]", rating.style)} {rating.name or "AI"} concludes {hilight(f"{rating.conclusion} ({rating.score}): {rating.comment}", rating.style)} for listing {hilight(listing.title)}."""
                        )
                # notification status?
                users_to_notify = (
                    item_config.notify
                    or marketplace_config.notify
                    or list(self.config.user.keys())
                )
                # for notification usages
                listing.name = item_config.name
                for user in users_to_notify:
                    ns = User(self.config.user[user], self.logger).notification_status(listing)
                    if self.logger:
                        if ns == NotificationStatus.NOTIFIED:
                            self.logger.info(
                                f"""{hilight("[Notify]", "succ")} Notified {user} about {post_url}."""
                            )
                        elif ns == NotificationStatus.EXPIRED:
                            self.logger.info(
                                f"""{hilight("[Notify]", "info")} Already notified {user} about {post_url}. The notification is ow expired."""
                            )
                        elif ns == NotificationStatus.LISTING_CHANGED:
                            self.logger.info(
                                f"""{hilight("[Notify]", "info")} Already notified {user} about {post_url}, but the listing is now changed."""
                            )
                        elif ns == NotificationStatus.LISTING_DISCOUNTED:
                            self.logger.info(
                                f"""{hilight("[Notify]", "info")} Already notified {user} about {post_url}, but the listing is now discounted."""
                            )
                        else:
                            self.logger.info(
                                f"""{hilight("[Notify]", "info")} Not notified {user} about {post_url} yet."""
                            )

                    # testing notification
                    # User(self.config.user[user], logger=self.logger).notify(
                    #     [listing], [rating], item_config, force=True
                    # )

    def evaluate_by_ai(
        self: "MarketplaceMonitor",
        item: Listing,
        item_config: TItemConfig,
        marketplace_config: TMarketplaceConfig,
    ) -> AIResponse:
        if item_config.ai is not None:
            ai_agents = item_config.ai
        elif marketplace_config.ai is not None:
            ai_agents = marketplace_config.ai
        else:
            ai_agents = None
        #
        for agent in self.ai_agents:
            if ai_agents is not None and agent.config.name not in ai_agents:
                continue
            try:
                return agent.evaluate(item, item_config, marketplace_config)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                if self.logger:
                    self.logger.error(
                        f"""{hilight("[AI]", "fail")} Failed to get an answer from {agent.config.name}: {e}"""
                    )
                continue
        return AIResponse(5, AIResponse.NOT_EVALUATED)
