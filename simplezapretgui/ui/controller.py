"""Контроллер: общее состояние приложения, фоновый мониторинг и все действия."""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from ..core import log, updater
from ..core.lists import ListManager
from ..core.paths import ZAPRET_DIR
from ..core.store import Results, Settings
from ..core.strategy import natural_key
from ..core.tester import check_https, ensure_targets_file
from ..core.zapret import Status, Zapret
from .widgets import run_bg

HEALTH_TARGETS = (("Discord", "discord.com", "/", 0),
                  ("YouTube", "www.youtube.com", "/", 40 * 1024),
                  ("Видео YouTube", "redirector.googlevideo.com", "/", 0))


class Controller(QObject):
    status_changed = Signal(object)          # Status
    strategies_changed = Signal()
    busy_changed = Signal(bool, str)
    log_entry = Signal(object)
    update_available = Signal(str, str)      # remote, local
    update_progress = Signal(int, int)
    health_changed = Signal(dict)
    error_raised = Signal(str)
    installed_changed = Signal(bool)
    test_progress = Signal(int, int, str)    # сделано, всего, имя (для трея и панели задач)
    test_finished = Signal()
    health_checking = Signal(bool)
    app_update_available = Signal(object)   # AppRelease — новая версия SimpleZapretGUI
    quit_for_update = Signal()              # установщик запущен — приложению пора закрыться
    builder_finished = Signal()             # подбор стратегии закончен

    def __init__(self):
        super().__init__()
        self.settings = Settings()
        self.results = Results()
        self.zap = Zapret(ZAPRET_DIR)
        self.zap.auto_tcp_ts = bool(self.settings.get("tcp_ts_auto"))
        self.status: Status = Status()
        self.health: dict = {}
        self.latest_version = ""
        self._busy = ""
        self._status_lock = threading.Lock()
        self._errors: list[str] = []
        self._expect_stop = False
        self._health_busy = False
        self.testing = False
        log.subscribe(lambda e: self.log_entry.emit(e))

        self.poll = QTimer(self)
        self.poll.setInterval(2500)
        self.poll.timeout.connect(self.refresh_status)
        self.health_timer = QTimer(self)
        self.health_timer.timeout.connect(self._periodic_health)

    # ------------------------------------------------------------ жизненный цикл
    def start(self):
        if self.zap.installed():
            self.zap.disable_bat_update_check()
            self.zap.ensure_user_lists()
            ensure_targets_file(self.zap.utils_dir)
        self.refresh_status()
        self.poll.start()
        self.health_timer.start(max(20, int(self.settings.get("health_interval"))) * 1000)
        QTimer.singleShot(1500, self.check_health)
        if self.settings.get("check_updates_on_start") or not self.zap.installed():
            QTimer.singleShot(800, lambda: self.check_updates(silent=True))
        if self.settings.get("check_updates_on_start"):
            QTimer.singleShot(2500, lambda: self.check_app_update(silent=True))
        resume = self.settings.get("resume_after_update")
        if resume and self.zap.installed():
            # обход работал до обновления программы — включаем его снова
            self.settings.set("resume_after_update", "")
            QTimer.singleShot(1500, lambda: self.connect(resume, "session"))
        elif self.zap.installed() and self.settings.get("auto_connect_on_start"):
            QTimer.singleShot(1200, self._auto_connect)

    def _auto_connect(self):
        st = self.status
        if not st.running and self.settings.get("last_strategy"):
            self.connect(self.settings.get("last_strategy"))

    # ------------------------------------------------------------ состояние
    @property
    def busy(self) -> str:
        return self._busy

    def _set_busy(self, text: str):
        self._busy = text
        self.busy_changed.emit(bool(text), text)
        if text:
            log.busy(text)

    def refresh_status(self):
        if not self._status_lock.acquire(blocking=False):
            return

        def work():
            try:
                return self.zap.status()
            finally:
                self._status_lock.release()

        run_bg(work, self._on_status, lambda e: None)

    def _on_status(self, st: Status):
        was_running = self.status.running
        self.status = st
        self.status_changed.emit(st)
        if was_running and not st.running and not self._busy:
            if self._expect_stop:
                self._expect_stop = False
            else:
                log.warn("Обход остановился")
            self.health = {}
            self.health_changed.emit({})
        elif not st.running and not self._busy:
            self._expect_stop = False

    def errors(self) -> list[str]:
        return list(self._errors)

    def push_error(self, msg: str):
        self._errors.append(msg)
        del self._errors[:-5]
        log.error(msg)
        self.error_raised.emit(msg)

    def clear_errors(self):
        self._errors.clear()
        self.error_raised.emit("")

    # ------------------------------------------------------------ стратегии
    def sorted_strategies(self) -> list[dict]:
        """Если есть результаты тестов — лучшие сверху, иначе по алфавиту (как service.bat)."""
        items = []
        ver = self.zap.version()
        for s in self.zap.strategies():
            r = self.results.get(s.name)
            items.append({"name": s.name, "user": s.user, "error": s.error,
                          "score": r["score"] if r else None,
                          "stale": bool(r and r.get("zapret_version") != ver),
                          "tested": r["time"] if r else 0})
        if any(i["score"] is not None for i in items):
            items.sort(key=lambda i: (i["score"] is None, -(i["score"] or 0), natural_key(i["name"])))
        return items

    def best_strategy(self) -> str:
        items = [i for i in self.sorted_strategies() if i["score"] is not None]
        return items[0]["name"] if items else ""

    def list_manager(self) -> ListManager:
        return ListManager(self.zap.lists_dir)

    # ------------------------------------------------------------ действия
    def _action(self, busy_text: str, fn: Callable, ok_text: str = "",
                after: Optional[Callable] = None, finished: Optional[Callable] = None) -> bool:
        if self._busy:
            log.warn(f"Подождите: {self._busy}")
            if finished:
                finished()
            return False
        self._set_busy(busy_text)

        def done(r):
            self._set_busy("")
            if ok_text:
                log.ok(ok_text if not isinstance(r, str) or not r else f"{ok_text}: {r}")
            self.refresh_status()
            if finished:
                finished()
            if after:
                after(r)

        def fail(msg):
            self._set_busy("")
            self.push_error(msg)
            self.refresh_status()
            if finished:
                finished()

        run_bg(fn, done, fail)
        return True

    def task(self, busy_text: str, fn: Callable, ok: Optional[Callable] = None,
             err_prefix: str = "", finished: Optional[Callable] = None):
        """Лёгкая фоновая операция, не блокирующая остальные (IPSet, hosts, кэш Discord...)."""
        log.busy(busy_text)

        def done(r):
            if ok:
                msg = ok(r)
                if msg:
                    log.ok(msg)
            if finished:
                finished()

        def fail(msg):
            self.push_error(f"{err_prefix}{msg}")
            if finished:
                finished()

        run_bg(fn, done, fail)

    def connect(self, name: str, mode: Optional[str] = None):
        mode = mode or self.settings.get("mode")
        s = self.zap.strategy(name)
        if s is None:
            self.push_error(f"Стратегия «{name}» не найдена")
            return
        self.settings.set("last_strategy", name)
        self.clear_errors()
        self._expect_stop = True

        def work():
            if mode == "service":
                self.zap.service_install(s)
            else:
                st = self.zap.status()
                if st.service_state == "RUNNING":
                    self.zap.service_remove()
                self.zap.start(s)
            return name

        self._action(f"Запуск «{name}»...", work,
                     "Служба запущена" if mode == "service" else "Обход запущен",
                     after=lambda _: QTimer.singleShot(2000, self.check_health))

    def disconnect(self):
        st = self.status

        def work():
            if st.service_exists and self.settings.get("mode") == "service":
                self.zap.service_remove()
            else:
                self.zap.stop_all(unload_driver=True)

        self.health = {}
        self.health_changed.emit({})
        self._expect_stop = True
        self._action("Остановка...", work, after=lambda _: log.warn("Обход остановлен"))

    def unload_driver(self, finished: Optional[Callable] = None):
        self._action("Выгрузка драйвера WinDivert...", self.zap.unload_driver, "Драйвер WinDivert выгружен",
                     finished=finished)

    def remove_conflicts(self, finished: Optional[Callable] = None):
        from ..core.diagnostics import apply_fix
        self._action("Удаление конфликтующих служб...", lambda: apply_fix("conflicts", self.zap), "Готово",
                     finished=finished)

    # ------------------------------------------------------------ быстрые действия
    def clear_discord(self, finished: Optional[Callable] = None):
        from ..core.diagnostics import clear_discord_cache
        self.task("Очистка кэша Discord...", clear_discord_cache, lambda r: "; ".join(r),
                  "Кэш Discord: ", finished)

    def update_ipset(self, finished: Optional[Callable] = None):
        from ..core.paths import IPSET_URL

        def work():
            data = updater.http_get(IPSET_URL, timeout=30).decode("utf-8", errors="ignore")
            if len(data.splitlines()) < 10:
                raise RuntimeError("получен пустой список")
            self.zap.write_ipset_list(data)
            return len(data.splitlines())

        def ok(n):
            self.installed_changed.emit(True)
            return f"Список IPSet обновлён: {n} записей"

        self.task("Загрузка списка IPSet...", work, ok, "IPSet не обновлён: ", finished)

    def update_hosts(self, finished: Optional[Callable] = None):
        from ..core import diagnostics
        self.task("Обновление hosts...", lambda: diagnostics.hosts_apply(diagnostics.fetch_hosts_block()),
                  lambda _: "Файл hosts обновлён", "hosts не обновлён: ", finished)

    def restart_if_running(self, reason: str = ""):
        st = self.status
        if not st.running or not self.settings.get("auto_restart_on_change"):
            if st.running:
                log.warn("Изменения применятся после перезапуска обхода")
            return
        name = st.strategy or self.settings.get("last_strategy")
        if not name:
            return
        mode = "service" if st.source == "service" else "session"
        log.info(f"Перезапуск для применения изменений{': ' + reason if reason else ''}")
        self.connect(name, mode)

    def shutdown_everything(self, finished: Optional[Callable] = None):
        """Выключить всё, что сейчас обходит блокировки: служба, winws, конфликтующие обходы, драйвер."""
        self._expect_stop = True
        self.health = {}
        self.health_changed.emit({})
        self._action("Выключение...", self.zap.shutdown_everything,
                     after=lambda r: log.warn(f"Обход выключен{': ' + r if r else ''}"), finished=finished)

    def set_mode(self, mode: str):
        """Сеанс или служба. Если обход уже работает от приложения — перезапустить в новом режиме."""
        self.settings.set("mode", mode)
        st = self.status
        if st.running and st.source in ("app", "service") and (st.source == "service") != (mode == "service"):
            name = st.strategy or self.settings.get("last_strategy")
            if name:
                self.connect(name, mode)

    def install_service(self, name: str, finished: Optional[Callable] = None):
        s = self.zap.strategy(name)
        if not s:
            if finished:
                finished()
            return
        self.settings.set("mode", "service")
        self.settings.set("last_strategy", name)
        self._expect_stop = True
        self._action("Установка службы...", lambda: self.zap.service_install(s), "Служба zapret установлена",
                     finished=finished)

    def remove_service(self, finished: Optional[Callable] = None):
        self._expect_stop = True
        self._action("Удаление службы...", self.zap.service_remove, "Службы удалены", finished=finished)

    def migrate_foreign_service(self, finished: Optional[Callable] = None):
        """Служба zapret смотрит в другую папку — переустановить её на управляемую копию."""
        name = self.status.service_strategy or self.settings.get("last_strategy") or "general"

        def work():
            self.zap.service_remove()
            s = self.zap.strategy(name) or self.zap.strategy("general")
            self.zap.service_install(s)
            return s.name

        self._expect_stop = True
        self._action("Перенос службы в SimpleZapretGUI...", work, "Служба перенесена", finished=finished)

    # ------------------------------------------------------------ TCP timestamps
    def tcp_ts_state(self, done: Callable):
        """Прочитать текущее состояние TCP timestamps в фоне: done(True/False/None)."""
        from ..core import winutil
        run_bg(winutil.tcp_timestamps, done, lambda e: done(None))

    def set_tcp_ts(self, on: bool, done: Callable):
        from ..core import winutil

        def ok(state):
            if state is on:
                log.ok("TCP timestamps " + ("включены" if on else "выключены"))
            elif state is None:
                self.push_error("TCP timestamps: не удалось проверить, применилось ли изменение")
            else:
                self.push_error("Windows не применила изменение TCP timestamps — они по-прежнему "
                                + ("выключены" if on else "включены"))
            done(state)

        def fail(msg):
            self.push_error(f"TCP timestamps: {msg}")
            done(None)

        log.busy("TCP timestamps: " + ("включение..." if on else "выключение..."))
        run_bg(lambda: winutil.set_tcp_timestamps(on), ok, fail)

    def set_tcp_ts_auto(self, on: bool):
        self.settings.set("tcp_ts_auto", on)
        self.zap.auto_tcp_ts = on

    # ------------------------------------------------------------ здоровье
    def _periodic_health(self):
        if self.status.running and not self._busy:
            self.check_health()

    def check_health(self):
        if self._health_busy:
            return
        self._health_busy = True
        self.health_checking.emit(True)

        def work():
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(3) as ex:
                futs = {t: ex.submit(check_https, h, p, None, 5, b) for t, h, p, b in HEALTH_TARGETS}
                return {t: f.result() for t, f in futs.items()}

        def done(r):
            self._health_busy = False
            self.health = r
            self.health_checking.emit(False)
            self.health_changed.emit(r)

        def fail(_):
            self._health_busy = False
            self.health_checking.emit(False)

        run_bg(work, done, fail)

    # ------------------------------------------------------------ обновления
    def check_updates(self, silent: bool = False, finished: Optional[Callable] = None):
        local = self.zap.version()
        if not silent:
            log.busy("Проверка обновлений zapret...")
        else:
            log.info("Проверка обновлений zapret...")

        def done(remote: str):
            self.latest_version = remote
            if not self.zap.installed():
                log.info(f"zapret не установлен. Последняя версия: {remote}")
                self.update_available.emit(remote, "")
            elif updater.is_newer(remote, local):
                log.warn(f"Доступна новая версия zapret: {remote} (установлена {local})")
                self.update_available.emit(remote, local)
            else:
                log.ok(f"zapret {local} — актуальная версия")
                self.update_available.emit("", local)
            if finished:
                finished()

        def fail(msg):
            log.warn(f"Не удалось проверить обновления: {msg}")
            if not self.zap.installed():
                # показываем баннер установки даже без сети (ZIP / папка)
                self.update_available.emit("", "")
            if finished:
                finished()

        run_bg(updater.latest_version, done, fail)

    # ------------------------------------------------------------ обновление самой программы
    def check_app_update(self, silent: bool = True, finished: Optional[Callable] = None):
        from ..core import selfupdate
        from ..core.paths import APP_VERSION

        def done(rel):
            if rel and selfupdate.is_newer(rel.version):
                if silent and rel.version == self.settings.get("skipped_app_version"):
                    log.info(f"Доступна SimpleZapretGUI {rel.version} (отложено пользователем)")
                else:
                    log.warn(f"Доступна новая версия SimpleZapretGUI {rel.version} (у вас {APP_VERSION})")
                    self.app_update_available.emit(rel)
            elif not silent:
                log.ok(f"SimpleZapretGUI {APP_VERSION} — последняя версия")
            if finished:
                finished()

        def fail(msg):
            (log.info if silent else log.warn)(f"Не удалось проверить обновления SimpleZapretGUI: {msg}")
            if finished:
                finished()

        run_bg(selfupdate.latest_release, done, fail)

    def install_app_update(self, rel, finished: Optional[Callable] = None):
        """Скачать установщик новой версии, запустить его и закрыть приложение."""
        import sys
        from ..core import selfupdate, winutil
        if not getattr(sys, "frozen", False):
            winutil.open_path(rel.page)          # запуск из исходников: только открыть страницу релиза
            if finished:
                finished()
            return

        def work():
            path = selfupdate.download_installer(rel, lambda d, n: self.update_progress.emit(d, n))
            return path

        def after(path):
            self.update_progress.emit(0, 0)
            st = self.status
            if st.running and st.source == "app" and st.strategy:
                self.settings.set("resume_after_update", st.strategy)
            log.busy(f"Установка SimpleZapretGUI {rel.version}... Программа перезапустится сама")
            winutil.release_mutex()
            selfupdate.run_installer(path)
            QTimer.singleShot(400, self.quit_for_update.emit)

        self._action(f"Загрузка SimpleZapretGUI {rel.version}...", work, "", after, finished)

    def install_update(self, tag: str = "", finished: Optional[Callable] = None):
        tag = tag or self.latest_version

        def work():
            t = tag or updater.latest_version()
            return updater.download_and_install(
                t, self.zap,
                progress=lambda d, n: self.update_progress.emit(d, n),
                progress_text=lambda s: log.busy(s))

        def after(r):
            self._after_install(r)

        verb = "Обновление" if self.zap.installed() else "Установка"
        self._action(f"{verb} zapret {tag}...", work, "", after, finished)

    def install_from_zip(self, path: str, finished: Optional[Callable] = None):
        from pathlib import Path
        self._action("Установка из архива...",
                     lambda: updater.install_from_zip(Path(path), self.zap, lambda s: log.busy(s)),
                     "", self._after_install, finished)

    def install_from_folder(self, path: str, finished: Optional[Callable] = None):
        from pathlib import Path
        self._action("Импорт папки zapret...",
                     lambda: updater.install_from_folder(Path(path), self.zap, lambda s: log.busy(s)),
                     "", self._after_install, finished)

    def _after_install(self, r: dict):
        ensure_targets_file(self.zap.utils_dir)
        log.ok(f"zapret {r.get('version')} установлен, настройки перенесены"
               + (f"; восстановлено: {r['restored']}" if r.get("restored") else ""))
        self.update_available.emit("", r.get("version", ""))
        self.installed_changed.emit(True)
        self.strategies_changed.emit()
        self.update_progress.emit(0, 0)

    # ------------------------------------------------------------ результаты тестов
    def save_result(self, r):
        self.results.put(r.name, r.score, r.ok, r.total, self.zap.version(), r.details)
        self.strategies_changed.emit()

    def set_busy_external(self, text: str):
        self._set_busy(text)

    def wait_until(self, cond: Callable[[], bool], timeout: float = 10) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if cond():
                return True
            time.sleep(0.2)
        return False
