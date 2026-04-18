import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from logging import Logger
from typing import Any, ClassVar, Generic, Optional, Type, TypeVar

from diskcache import Cache  # type: ignore
from openai import OpenAI  # type: ignore
from rich.pretty import pretty_repr

from .listing import Listing
from .marketplace import TItemConfig, TMarketplaceConfig
from .utils import BaseConfig, CacheType, CounterItem, cache, counter, hilight


class AIServiceProvider(Enum):
    OPENAI = "OpenAI"
    DEEPSEEK = "DeepSeek"
    ANTHROPIC = "Anthropic"
    OLLAMA = "Ollama"


@dataclass
class AIResponse:
    score: int
    comment: str
    name: str = ""

    NOT_EVALUATED: ClassVar = "Not evaluated by AI"

    @property
    def conclusion(self: "AIResponse") -> str:
        return {
            1: "No match",
            2: "Potential match",
            3: "Poor match",
            4: "Good match",
            5: "Great deal",
        }[self.score]

    @property
    def style(self: "AIResponse") -> str:
        if self.comment == self.NOT_EVALUATED:
            return "dim"
        if self.score < 3:
            return "fail"
        if self.score > 3:
            return "succ"
        return "name"

    @property
    def stars(self: "AIResponse") -> str:
        full_stars = self.score
        empty_stars = 5 - full_stars
        return (
            '<span style="color: #FFD700; font-size: 20px;">★</span>' * full_stars
            + '<span style="color: #D3D3D3; font-size: 20px;">☆</span>' * empty_stars
        )

    @classmethod
    def from_cache(
        cls: Type["AIResponse"],
        listing: Listing,
        item_config: TItemConfig,
        marketplace_config: TMarketplaceConfig,
        local_cache: Cache | None = None,
    ) -> Optional["AIResponse"]:
        res = (cache if local_cache is None else local_cache).get(
            (CacheType.AI_INQUIRY.value, item_config.hash, marketplace_config.hash, listing.hash)
        )
        if res is None:
            return None
        return AIResponse(**res)

    def to_cache(
        self: "AIResponse",
        listing: Listing,
        item_config: TItemConfig,
        marketplace_config: TMarketplaceConfig,
        local_cache: Cache | None = None,
    ) -> None:
        (cache if local_cache is None else local_cache).set(
            (CacheType.AI_INQUIRY.value, item_config.hash, marketplace_config.hash, listing.hash),
            asdict(self),
            tag=CacheType.AI_INQUIRY.value,
        )


@dataclass
class AIConfig(BaseConfig):
    # this argument is required

    api_key: str | None = None
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    max_retries: int = 10
    timeout: int | None = None

    def handle_provider(self: "AIConfig") -> None:
        if self.provider is None:
            return
        if self.provider.lower() not in [x.value.lower() for x in AIServiceProvider]:
            raise ValueError(
                f"""AIConfig requires a valid service provider. Valid providers are {hilight(", ".join([x.value for x in AIServiceProvider]))}"""
            )

    def handle_api_key(self: "AIConfig") -> None:
        if self.api_key is None:
            return
        if not isinstance(self.api_key, str):
            raise ValueError("AIConfig requires a string api_key.")
        self.api_key = self.api_key.strip()

    def handle_max_retries(self: "AIConfig") -> None:
        if not isinstance(self.max_retries, int) or self.max_retries < 0:
            raise ValueError("AIConfig requires a positive integer max_retries.")

    def handle_timeout(self: "AIConfig") -> None:
        if self.timeout is None:
            return
        if not isinstance(self.timeout, int) or self.timeout < 0:
            raise ValueError("AIConfig requires a positive integer timeout.")


@dataclass
class OpenAIConfig(AIConfig):
    def handle_api_key(self: "OpenAIConfig") -> None:
        if self.api_key is None:
            raise ValueError("OpenAI requires a string api_key.")


@dataclass
class DeekSeekConfig(OpenAIConfig):
    pass


@dataclass
class OllamaConfig(OpenAIConfig):
    api_key: str | None = field(default="ollama")  # required but not used.

    def handle_base_url(self: "OllamaConfig") -> None:
        if self.base_url is None:
            raise ValueError("Ollama requires a string base_url.")

    def handle_model(self: "OllamaConfig") -> None:
        if self.model is None:
            raise ValueError("Ollama requires a string model.")


@dataclass
class AnthropicConfig(AIConfig):
    def handle_api_key(self: "AnthropicConfig") -> None:
        if self.api_key is None:
            raise ValueError("Anthropic requires a string api_key.")


TAIConfig = TypeVar("TAIConfig", bound=AIConfig)


class AIBackend(Generic[TAIConfig]):
    def __init__(self: "AIBackend", config: AIConfig, logger: Logger | None = None) -> None:
        self.config = config
        self.logger = logger
        self.client: Any = None

    @classmethod
    def get_config(cls: Type["AIBackend"], **kwargs: Any) -> TAIConfig:
        raise NotImplementedError("get_config method must be implemented by subclasses.")

    def connect(self: "AIBackend") -> None:
        raise NotImplementedError("Connect method must be implemented by subclasses.")

    def get_prompt(
        self: "AIBackend",
        listing: Listing,
        item_config: TItemConfig,
        marketplace_config: TMarketplaceConfig,
    ) -> str:
        prompt = (
            f"""用户想在 Facebook Marketplace 购买 {item_config.name}。"""
            f"""搜索词："{'、'.join(item_config.search_phrases)}"。"""
        )
        if item_config.description:
            prompt += f"""需求描述："{item_config.description}"。"""
        #
        max_price = item_config.max_price or 0
        min_price = item_config.min_price or 0
        if max_price and min_price:
            prompt += f"""价格范围：{min_price} 到 {max_price}。"""
        elif max_price:
            prompt += f"""最高价：{max_price}。"""
        elif min_price:
            prompt += f"""最低价：{min_price}。"""
        #
        if item_config.antikeywords:
            prompt += f"""标题或描述中必须排除关键词："{'、'.join(item_config.antikeywords)}"。"""
        if getattr(item_config, "ai_keywords", False) and item_config.keywords:
            keywords_list = item_config.keywords if isinstance(item_config.keywords, list) else [item_config.keywords]
            prompt += (
                f""" 标题和描述必须在语义上满足以下关键词条件："{'; '.join(keywords_list)}"。"""
                """不满足时直接评为 1 分。"""
            )
        #
        prompt += (
            f"""\n\n用户找到的商品信息："""
            # f"""priced at {listing.price}, located in {listing.location}, """
            # f"""posted at {listing.post_url} with description "{listing.description}"\n\n"""
            f"""标题：{listing.title}；"""
            f"""描述："{listing.description}"\n\n"""
        )
        # prompt
        if item_config.prompt is not None:
            prompt += item_config.prompt
        elif marketplace_config.prompt is not None:
            prompt += marketplace_config.prompt
        else:
            prompt += "请判断该商品与用户需求的匹配度，重点考虑描述、价格合理性、年份、成色和卖家可信度。"
        # extra_prompt
        prompt += "\n"
        if item_config.extra_prompt is not None:
            prompt += f"\n{item_config.extra_prompt.strip()}\n"
        elif marketplace_config.extra_prompt is not None:
            prompt += f"\n{marketplace_config.extra_prompt.strip()}\n"
        # rating_prompt
        if item_config.rating_prompt is not None:
            prompt += f"\n{item_config.rating_prompt.strip()}\n"
        elif marketplace_config.rating_prompt is not None:
            prompt += f"\n{marketplace_config.rating_prompt.strip()}\n"
        else:
            prompt += (
                "\n请按 1 到 5 分打分：\n"
                "1 - 不匹配：关键信息缺失、类目错误，或存在可疑行为。\n"
                "2 - 可能匹配：信息不完整，需要进一步确认。\n"
                "3 - 一般：部分符合，但有明显不符点或缺失。\n"
                "4 - 较好：大部分符合，信息清晰。\n"
                "5 - 非常好：高度匹配，且价格或成色优秀。\n"
                "最后一行必须使用：\n"
                '"Rating <1-5>: <30字以内建议>"'
            )
        if self.logger:
            self.logger.debug(f"""{hilight("[AI-Prompt]", "info")} {prompt}""")
        return prompt

    def evaluate(
        self: "AIBackend",
        listing: Listing,
        item_config: TItemConfig,
        marketplace_config: TMarketplaceConfig,
    ) -> AIResponse:
        raise NotImplementedError("Confirm method must be implemented by subclasses.")


class OpenAIBackend(AIBackend):
    default_model = "gpt-4o"
    # the default is f"https://api.openai.com/v1"
    base_url: str | None = None

    @classmethod
    def get_config(cls: Type["OpenAIBackend"], **kwargs: Any) -> OpenAIConfig:
        return OpenAIConfig(**kwargs)

    def connect(self: "OpenAIBackend") -> None:
        if self.client is None:
            self.client = OpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url or self.base_url,
                timeout=self.config.timeout,
                default_headers={
                    "X-Title": "AI Marketplace Monitor",
                    "HTTP-Referer": "https://github.com/BoPeng/ai-marketplace-monitor",
                },
            )
            if self.logger:
                self.logger.info(f"""{hilight("[AI]", "name")} {self.config.name} connected.""")

    def evaluate(
        self: "OpenAIBackend",
        listing: Listing,
        item_config: TItemConfig,
        marketplace_config: TMarketplaceConfig,
    ) -> AIResponse:
        # ask openai to confirm the item is correct
        counter.increment(CounterItem.AI_QUERY, item_config.name)
        prompt = self.get_prompt(listing, item_config, marketplace_config)
        res: AIResponse | None = AIResponse.from_cache(listing, item_config, marketplace_config)
        if res is not None:
            if self.logger:
                self.logger.debug(
                    f"""{hilight("[AI]", res.style)} {self.config.name} previously concluded {hilight(f"{res.conclusion} ({res.score}): {res.comment}", res.style)} for listing {hilight(listing.title)}."""
                )
            return res

        self.connect()

        retries = 0
        while retries < self.config.max_retries:
            self.connect()
            assert self.client is not None
            try:
                response = self.client.chat.completions.create(
                    model=self.config.model or self.default_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a helpful assistant that can confirm if a user's search criteria matches the item he is interested in.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    stream=False,
                )
                break
            except KeyboardInterrupt:
                raise
            except Exception as e:
                if self.logger:
                    self.logger.error(
                        f"""{hilight("[AI-Error]", "fail")} {self.config.name} failed to evaluate {hilight(listing.title)}: {e}"""
                    )
                retries += 1
                # try to initiate a connection
                self.client = None
                time.sleep(5)

        # check if the response is yes
        if self.logger:
            self.logger.debug(f"""{hilight("[AI-Response]", "info")} {pretty_repr(response)}""")

        answer = response.choices[0].message.content or ""
        if (
            answer is None
            or not answer.strip()
            or re.search(r"Rating[^1-5]*[1-5]", answer, re.DOTALL) is None
        ):
            counter.increment(CounterItem.FAILED_AI_QUERY, item_config.name)
            raise ValueError(f"Empty or invalid response from {self.config.name}: {response}")

        lines = answer.split("\n")
        # if any of the lines contains "Rating: ", extract the rating from it.
        score: int = 1
        comment = ""
        rating_line = None
        for idx, line in enumerate(lines):
            matched = re.match(r".*Rating[^1-5]*([1-5])[:\s]*(.*)", line)
            if matched:
                score = int(matched.group(1))
                comment = matched.group(2).strip()
                rating_line = idx
                continue
            if rating_line is not None:
                # if the AI puts comment after Rating, we need to include them
                comment += " " + line
        # if the AI puts the rating at the end, let us try to use the line before the Rating line
        if len(comment.strip()) < 5 and rating_line is not None and rating_line > 0:
            comment = lines[rating_line - 1]

        # remove multiple spaces, take first 30 words
        comment = " ".join([x for x in comment.split() if x.strip()]).strip()
        res = AIResponse(name=self.config.name, score=score, comment=comment)
        res.to_cache(listing, item_config, marketplace_config)
        counter.increment(CounterItem.NEW_AI_QUERY, item_config.name)
        return res


class DeepSeekBackend(OpenAIBackend):
    default_model = "deepseek-chat"
    base_url = "https://api.deepseek.com"

    @classmethod
    def get_config(cls: Type["DeepSeekBackend"], **kwargs: Any) -> DeekSeekConfig:
        return DeekSeekConfig(**kwargs)


class OllamaBackend(OpenAIBackend):
    default_model = "deepseek-r1:14b"

    @classmethod
    def get_config(cls: Type["OllamaBackend"], **kwargs: Any) -> OllamaConfig:
        return OllamaConfig(**kwargs)


class AnthropicBackend(AIBackend):
    default_model = "claude-sonnet-4-20250514"

    @classmethod
    def get_config(cls: Type["AnthropicBackend"], **kwargs: Any) -> AnthropicConfig:
        return AnthropicConfig(**kwargs)

    def connect(self: "AnthropicBackend") -> None:
        if self.client is None:
            import anthropic  # type: ignore

            self.client = anthropic.Anthropic(
                api_key=self.config.api_key,
                timeout=self.config.timeout,
            )
            if self.logger:
                self.logger.info(f"""{hilight("[AI]", "name")} {self.config.name} connected.""")

    def evaluate(
        self: "AnthropicBackend",
        listing: Listing,
        item_config: TItemConfig,
        marketplace_config: TMarketplaceConfig,
    ) -> AIResponse:
        counter.increment(CounterItem.AI_QUERY, item_config.name)
        prompt = self.get_prompt(listing, item_config, marketplace_config)
        res: AIResponse | None = AIResponse.from_cache(listing, item_config, marketplace_config)
        if res is not None:
            if self.logger:
                self.logger.debug(
                    f"""{hilight("[AI]", res.style)} {self.config.name} previously concluded {hilight(f"{res.conclusion} ({res.score}): {res.comment}", res.style)} for listing {hilight(listing.title)}."""
                )
            return res

        self.connect()

        retries = 0
        while retries < self.config.max_retries:
            self.connect()
            assert self.client is not None
            try:
                response = self.client.messages.create(
                    model=self.config.model or self.default_model,
                    max_tokens=1024,
                    system="You are a helpful assistant that can confirm if a user's search criteria matches the item he is interested in.",
                    messages=[
                        {"role": "user", "content": prompt},
                    ],
                )
                break
            except KeyboardInterrupt:
                raise
            except Exception as e:
                if self.logger:
                    self.logger.error(
                        f"""{hilight("[AI-Error]", "fail")} {self.config.name} failed to evaluate {hilight(listing.title)}: {e}"""
                    )
                retries += 1
                self.client = None
                time.sleep(5)

        if self.logger:
            self.logger.debug(f"""{hilight("[AI-Response]", "info")} {pretty_repr(response)}""")

        answer = response.content[0].text if response.content else ""
        if (
            answer is None
            or not answer.strip()
            or re.search(r"Rating[^1-5]*[1-5]", answer, re.DOTALL) is None
        ):
            counter.increment(CounterItem.FAILED_AI_QUERY, item_config.name)
            raise ValueError(f"Empty or invalid response from {self.config.name}: {response}")

        lines = answer.split("\n")
        score: int = 1
        comment = ""
        rating_line = None
        for idx, line in enumerate(lines):
            matched = re.match(r".*Rating[^1-5]*([1-5])[:\s]*(.*)", line)
            if matched:
                score = int(matched.group(1))
                comment = matched.group(2).strip()
                rating_line = idx
                continue
            if rating_line is not None:
                comment += " " + line
        if len(comment.strip()) < 5 and rating_line is not None and rating_line > 0:
            comment = lines[rating_line - 1]

        comment = " ".join([x for x in comment.split() if x.strip()]).strip()
        res = AIResponse(name=self.config.name, score=score, comment=comment)
        res.to_cache(listing, item_config, marketplace_config)
        counter.increment(CounterItem.NEW_AI_QUERY, item_config.name)
        return res
