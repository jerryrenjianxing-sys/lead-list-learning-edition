import importlib

import pytest
import typer

import config
from config import base_config
from cmd_arg.arg import parse_cmd


@pytest.mark.asyncio
async def test_search_has_no_hidden_default_keyword():
    assert importlib.reload(base_config).KEYWORDS == ""

    with pytest.raises(typer.BadParameter, match="keywords"):
        await parse_cmd(["--type", "search", "--keywords", "，,  "])
