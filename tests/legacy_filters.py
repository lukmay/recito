# -*- coding: utf-8 -*-
"""The former 'obsidian' filter, frozen as the reference for the golden
tests of the Markdown and Obsidian preset rules. Not used by the app."""
import re

from tts_filters import _conv_latex


def _md_wikilink(m):
    inner = m.group(1)
    if "|" in inner:                                   # [[target|alias]] -> alias
        return " " + inner.split("|", 1)[1].strip() + " "
    target = inner.split("#", 1)[0].split("/")[-1]     # drop path and #anchor
    target = re.sub(r"\.(md|pdf|png|jpe?g|gif|svg|canvas)$", "", target.strip(),
                    flags=re.I)
    return " " + target + " "


def obsidian(text):
    text = re.sub(r"^[ \t]*```[^\n]*$", "", text, flags=re.M)
    text = re.sub(r"\$\$(.+?)\$\$",
                  lambda m: ". " + _conv_latex(m.group(1)) + ". ", text, flags=re.S)
    text = re.sub(r"\$([^$\n]+?)\$",
                  lambda m: " " + _conv_latex(m.group(1)) + " ", text)
    text = re.sub(r"\[\[([^\]]+?)\]\]", _md_wikilink, text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+?)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^[ \t]*#{1,6}[ \t]+", "", text, flags=re.M)
    text = re.sub(r"^[ \t]*>[ \t]?", "", text, flags=re.M)
    text = re.sub(r"^[ \t]*[-*+][ \t]+", "", text, flags=re.M)
    text = re.sub(r"^[ \t]*\d+\.[ \t]+", "", text, flags=re.M)
    text = re.sub(r"^[ \t]*([-*_])\1{2,}[ \t]*$", "", text, flags=re.M)
    text = re.sub(r"\*\*([^*]+?)\*\*", r"\1", text)
    text = re.sub(r"\*([^*\n]+?)\*", r"\1", text)
    text = re.sub(r"~~([^~]+?)~~", r"\1", text)
    text = re.sub(r"`([^`\n]+?)`", r"\1", text)
    text = re.sub(r"__([^_]+?)__", r"\1", text)
    text = re.sub(r"(?<![\w\\])_([^_\n]+?)_(?![\w])", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([.,;:!?])", r"\1", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"(?m)^[ \t]+", "", text)
    return text.strip()
