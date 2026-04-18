from dataclasses import asdict, dataclass
from typing import Optional, Tuple, Type

from diskcache import Cache  # type: ignore

from .utils import CacheType, cache, hash_dict

# TTL for the excluded-listing cache: 7 days.  If a listing stays in search
# results longer than this (or config changes), it will be re-examined.
_EXCLUDED_TTL = 7 * 24 * 3600


@dataclass
class Listing:
    marketplace: str
    name: str
    # unique identification
    id: str
    title: str
    image: str
    price: str
    post_url: str
    location: str
    seller: str
    condition: str
    description: str

    @property
    def content(self: "Listing") -> Tuple[str, str, str]:
        return (self.title, self.description, self.price)

    @property
    def hash(self: "Listing") -> str:
        # we need to normalize post_url before hashing because post_url will be different
        # each time from a search page. We also does not count image
        return hash_dict(
            {
                x: (y.split("?")[0] if x == "post_url" else y)
                for x, y in asdict(self).items()
                if x != "image"
            }
        )

    @classmethod
    def from_cache(
        cls: Type["Listing"],
        post_url: str,
        local_cache: Cache | None = None,
    ) -> Optional["Listing"]:
        try:
            # details could be a different datatype, miss some key etc.
            # and we have recently changed to save Listing as a dictionary
            return cls(
                **(cache if local_cache is None else local_cache).get(
                    (CacheType.LISTING_DETAILS.value, post_url.split("?")[0])
                )
            )
        except KeyboardInterrupt:
            raise
        except Exception:
            return None

    def to_cache(
        self: "Listing",
        post_url: str,
        local_cache: Cache | None = None,
    ) -> None:
        (cache if local_cache is None else local_cache).set(
            (CacheType.LISTING_DETAILS.value, post_url.split("?")[0]),
            asdict(self),
            tag=CacheType.LISTING_DETAILS.value,
        )

    def mark_excluded(
        self: "Listing",
        post_url: str,
        local_cache: Cache | None = None,
    ) -> None:
        """Remember that this listing was excluded so it can be skipped next run."""
        (cache if local_cache is None else local_cache).set(
            (CacheType.LISTING_EXCLUDED.value, post_url.split("?")[0]),
            self.price,
            expire=_EXCLUDED_TTL,
            tag=CacheType.LISTING_EXCLUDED.value,
        )

    @classmethod
    def is_excluded(
        cls: Type["Listing"],
        post_url: str,
        price: str | None,
        local_cache: Cache | None = None,
    ) -> bool:
        """Return True if this listing was excluded previously with the same price."""
        cached_price = (cache if local_cache is None else local_cache).get(
            (CacheType.LISTING_EXCLUDED.value, post_url.split("?")[0])
        )
        return cached_price is not None and cached_price == price
