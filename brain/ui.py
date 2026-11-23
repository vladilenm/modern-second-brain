"""Ввод и вывод мастера настройки: список с галочками и простые вопросы.

TextUi работает в любом терминале и в тестах (потоки подставляются). CursesUi добавляет полноэкранный
список с галочками; если curses не справился (маленькое окно, странный терминал), список задаётся вопросами.
"""
from __future__ import annotations

import curses
import getpass
import locale
import os
import sys
from typing import Callable, List, Sequence, Set, TextIO, Tuple

YES = ("д", "да", "y", "yes")
NO = ("н", "нет", "n", "no")
Item = Tuple[str, str]  # название и подсказка справа


class TextUi:
    def __init__(self, inp: TextIO = None, out: TextIO = None, secret: Callable[[str], str] = None):
        self.inp = inp or sys.stdin
        self.out = out or sys.stdout
        self.secret = secret or getpass.getpass

    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def _line(self, prompt: str) -> str:
        self.out.write(prompt)
        self.out.flush()
        line = self.inp.readline()
        if not line:
            raise EOFError("ввод закончился")
        return line.rstrip("\n")

    def ask(self, label: str, default: str = "", help: str = "") -> str:
        if help:
            self.say(f"  {help}")
        shown = f" [{default}]" if default else ""
        return self._line(f"{label}{shown}: ").strip() or default

    def ask_secret(self, label: str) -> str:
        return self.secret(f"{label} (ввод скрыт): ").strip()

    def confirm(self, question: str, default: bool) -> bool:
        hint = "[Д/н]" if default else "[д/Н]"
        while True:
            answer = self._line(f"{question} {hint} ").strip().lower()
            if not answer:
                return default
            if answer in YES:
                return True
            if answer in NO:
                return False
            self.say("Ответь «д» или «н».")

    def choose(self, question: str, options: Sequence[str], default: int = 0) -> int:
        self.say(question)
        for number, option in enumerate(options, 1):
            self.say(f"  {number}. {option}")
        while True:
            answer = self._line(f"Номер [{default + 1}]: ").strip()
            if not answer:
                return default
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return int(answer) - 1
            self.say(f"Введи номер от 1 до {len(options)}.")

    def checklist(self, title: str, items: Sequence[Item], checked: Set[int]) -> List[int]:
        state = set(checked)
        while True:
            self.say(title)
            for number, (name, hint) in enumerate(items, 1):
                mark = "x" if number - 1 in state else " "
                self.say(f"  [{mark}] {number:>2}. {name:<16} {hint}".rstrip())
            answer = self._line("Номера через пробел — переключить, Enter — дальше: ").strip()
            if not answer:
                return sorted(state)
            for part in answer.replace(",", " ").split():
                if part.isdigit() and 1 <= int(part) <= len(items):
                    state ^= {int(part) - 1}
                else:
                    self.say(f"Нет пункта «{part}».")


class Checklist:
    """Состояние списка с галочками отдельно от curses — его проверяют тесты."""

    def __init__(self, items: Sequence[Item], checked: Set[int]):
        self.items = list(items)
        self.checked = set(checked)
        self.pos = 0

    def press(self, key: int) -> bool:
        """Обработать клавишу; True — выбор закончен."""
        if key in (curses.KEY_UP, ord("k")):
            self.pos = (self.pos - 1) % len(self.items)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.pos = (self.pos + 1) % len(self.items)
        elif key == ord(" "):
            self.checked ^= {self.pos}
        elif key in (curses.KEY_ENTER, 10, 13):
            return True
        return False

    def lines(self) -> List[str]:
        return [f"{'>' if i == self.pos else ' '} [{'x' if i in self.checked else ' '}] {name:<16} {hint}".rstrip()
                for i, (name, hint) in enumerate(self.items)]


class CursesUi(TextUi):
    def checklist(self, title: str, items: Sequence[Item], checked: Set[int]) -> List[int]:
        state = Checklist(items, checked)
        try:
            curses.wrapper(self._draw, title, state)
        except curses.error:
            return super().checklist(title, items, checked)
        return sorted(state.checked)

    @staticmethod
    def _draw(screen, title: str, state: Checklist) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass  # терминал не умеет прятать курсор — не страшно
        while True:
            screen.erase()
            width = screen.getmaxyx()[1] - 1
            screen.addstr(0, 0, title[:width], curses.A_BOLD)
            screen.addstr(1, 0, "↑↓ — выбрать, пробел — вкл/выкл, Enter — дальше"[:width])
            for row, line in enumerate(state.lines()):
                screen.addstr(row + 3, 0, line[:width], curses.A_REVERSE if row == state.pos else 0)
            screen.refresh()
            if state.press(screen.getch()):
                return


def make_ui() -> TextUi:
    """Полноэкранный список, если это настоящий терминал; иначе — простые вопросы."""
    if sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM", "dumb") != "dumb":
        locale.setlocale(locale.LC_ALL, "")  # иначе curses не выведет кириллицу
        return CursesUi()
    return TextUi()
