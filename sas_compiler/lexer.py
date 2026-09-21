"""Tokenizer for (macro-expanded) SAS source."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto


class TokType(Enum):
    IDENT = auto()
    NUMBER = auto()
    STRING = auto()
    OP = auto()
    SEMI = auto()
    LPAREN = auto()
    RPAREN = auto()
    COMMA = auto()
    EOF = auto()


@dataclass
class Token:
    type: TokType
    value: str
    pos: int

    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r})"


_MULTI_OPS = ["**", "<=", ">=", "^=", "~=", "ne", "||", "!!"]
_SINGLE_OPS = set("+-*/=<>()., ;")


class Lexer:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.n = len(text)

    def tokenize(self) -> list[Token]:
        toks = []
        while True:
            tok = self._next()
            toks.append(tok)
            if tok.type == TokType.EOF:
                break
        return toks

    def _skip_ws_comments(self):
        text = self.text
        while self.pos < self.n:
            c = text[self.pos]
            if c in " \t\r\n":
                self.pos += 1
                continue
            if c == "/" and text[self.pos:self.pos + 2] == "/*":
                j = text.find("*/", self.pos + 2)
                self.pos = (j + 2) if j != -1 else self.n
                continue
            break

    def _next(self) -> Token:
        self._skip_ws_comments()
        if self.pos >= self.n:
            return Token(TokType.EOF, "", self.pos)
        text = self.text
        start = self.pos
        c = text[self.pos]

        if c in "'\"":
            self.pos += 1
            buf = []
            while self.pos < self.n:
                if text[self.pos] == c:
                    if text[self.pos:self.pos + 2] == c * 2:
                        buf.append(c)
                        self.pos += 2
                        continue
                    self.pos += 1
                    break
                buf.append(text[self.pos])
                self.pos += 1
            return Token(TokType.STRING, "".join(buf), start)

        if c.isdigit() or (c == "." and self.pos + 1 < self.n and text[self.pos + 1].isdigit()):
            m = re.match(r"\d*\.?\d+([eE][+-]?\d+)?", text[self.pos:])
            self.pos += m.end()
            return Token(TokType.NUMBER, m.group(0), start)

        if c.isalpha() or c == "_":
            m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[self.pos:])
            self.pos += m.end()
            word = m.group(0)
            return Token(TokType.IDENT, word, start)

        if c == ";":
            self.pos += 1
            return Token(TokType.SEMI, ";", start)
        if c == "(":
            self.pos += 1
            return Token(TokType.LPAREN, "(", start)
        if c == ")":
            self.pos += 1
            return Token(TokType.RPAREN, ")", start)
        if c == ",":
            self.pos += 1
            return Token(TokType.COMMA, ",", start)

        for op in ["**", "<=", ">=", "^=", "~=", "||", "!!"]:
            if text[self.pos:self.pos + len(op)] == op:
                self.pos += len(op)
                return Token(TokType.OP, op, start)

        if c in "+-*/=<>":
            self.pos += 1
            return Token(TokType.OP, c, start)

        if c == "&" or c == "%":
            # leftover unresolved macro trigger; treat as identifier char run
            self.pos += 1
            return Token(TokType.OP, c, start)

        if c == "$":
            self.pos += 1
            return Token(TokType.OP, "$", start)

        # unknown char, skip
        self.pos += 1
        return Token(TokType.OP, c, start)
