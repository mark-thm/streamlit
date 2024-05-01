# Copyright (c) Streamlit Inc. (2018-2022) Snowflake Inc. (2022-2024)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import re
import threading
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Final, cast

from blinker import Signal

from streamlit.logger import get_logger
from streamlit.string_util import extract_leading_emoji
from streamlit.util import calc_md5

_LOGGER: Final = get_logger(__name__)


def open_python_file(filename: str):
    """Open a read-only Python file taking proper care of its encoding.

    In Python 3, we would like all files to be opened with utf-8 encoding.
    However, some author like to specify PEP263 headers in their source files
    with their own encodings. In that case, we should respect the author's
    encoding.
    """
    import tokenize

    if hasattr(tokenize, "open"):  # Added in Python 3.2
        # Open file respecting PEP263 encoding. If no encoding header is
        # found, opens as utf-8.
        return tokenize.open(filename)
    else:
        return open(filename, encoding="utf-8")


PAGE_FILENAME_REGEX = re.compile(r"([0-9]*)[_ -]*(.*)\.py")


def page_sort_key(script_path: Path) -> tuple[float, str]:
    matches = re.findall(PAGE_FILENAME_REGEX, script_path.name)

    # Failing this assert should only be possible if script_path isn't a Python
    # file, which should never happen.
    assert len(matches) > 0, f"{script_path} is not a Python file"

    [(number, label)] = matches
    label = label.lower()

    if number == "":
        return (float("inf"), label)

    return (float(number), label)


def page_icon_and_name(script_path: Path) -> tuple[str, str]:
    """Compute the icon and name of a page from its script path.

    This is *almost* the page name displayed in the nav UI, but it has
    underscores instead of spaces. The reason we do this is because having
    spaces in URLs both looks bad and is hard to deal with due to the need to
    URL-encode them. To solve this, we only swap the underscores for spaces
    right before we render page names.
    """
    extraction = re.search(PAGE_FILENAME_REGEX, script_path.name)
    if extraction is None:
        return "", ""

    # This cast to Any+type annotation weirdness is done because
    # cast(re.Match[str], ...) explodes at runtime since Python interprets it
    # as an attempt to index into re.Match instead of as a type annotation.
    extraction: re.Match[str] = cast(Any, extraction)

    icon_and_name = re.sub(
        r"[_ ]+", "_", extraction.group(2)
    ).strip() or extraction.group(1)

    return extract_leading_emoji(icon_and_name)


class MultipageAppsVersion(Enum):
    V1 = 1
    V2 = 2


class V1PagesManager:
    version: Final = MultipageAppsVersion.V1

    def __init__(self, parent):
        self._parent = parent

    def get_main_page(self):
        return {
            "script_path": self._parent._main_script_path,
            "script_hash": self._parent._main_script_hash,
        }

    def get_page_by_run(self, page_script_hash, page_name):
        pages = self.get_pages()
        # Safe because pages will at least contain the app's main page.
        main_page_info = list(pages.values())[0]

        if page_script_hash:
            current_page_info = pages.get(page_script_hash, None)
        elif not page_script_hash and page_name:
            # If a user navigates directly to a non-main page of an app, we get
            # the first script run request before the list of pages has been
            # sent to the frontend. In this case, we choose the first script
            # with a name matching the requested page name.
            current_page_info = next(
                filter(
                    # There seems to be this weird bug with mypy where it
                    # thinks that p can be None (which is impossible given the
                    # types of pages), so we add `p and` at the beginning of
                    # the predicate to circumvent this.
                    lambda p: p and (p["page_name"] == page_name),
                    pages.values(),
                ),
                None,
            )
        else:
            # If no information about what page to run is given, default to
            # running the main page.
            current_page_info = main_page_info

        return current_page_info

    def get_pages(self):
        main_script_path = Path(self._parent._main_script_path)
        main_script_hash = self._parent._main_script_hash
        main_page_icon, main_page_name = page_icon_and_name(main_script_path)

        # NOTE: We include the page_script_hash in the dict even though it is
        #       already used as the key because that occasionally makes things
        #       easier for us when we need to iterate over pages.
        pages = {
            main_script_hash: {
                "page_script_hash": main_script_hash,
                "page_name": main_page_name,
                "icon": main_page_icon,
                "script_path": str(main_script_path.resolve()),
            }
        }

        pages_dir = main_script_path.parent / "pages"
        page_scripts = sorted(
            [
                f
                for f in pages_dir.glob("*.py")
                if not f.name.startswith(".") and not f.name == "__init__.py"
            ],
            key=page_sort_key,
        )

        for script_path in page_scripts:
            script_path_str = str(script_path.resolve())
            pi, pn = page_icon_and_name(script_path)
            psh = calc_md5(script_path_str)

            pages[psh] = {
                "page_script_hash": psh,
                "page_name": pn,
                "icon": pi,
                "script_path": script_path_str,
            }

        return pages

    def set_pages(self, _):
        raise NotImplementedError("Cannot set pages in version 1 of multipage apps.")


class V2PagesManager:
    version: Final = MultipageAppsVersion.V2

    def __init__(self, parent):
        self._parent = parent
        self._pages = None

    def get_main_page(self):
        return {
            "script_path": self._parent._main_script_path,
            "script_hash": self._parent._main_script_hash,  # Default Hash
        }

    def get_page_by_run(self, page_script_hash, page_name):
        pages = self.get_pages()
        page_hash = None
        if page_script_hash:
            page_hash = page_script_hash
        elif not page_script_hash and page_name:
            # If a user navigates directly to a non-main page of an app, we get
            # the first script run request before the list of pages has been
            # sent to the frontend. In this case, we choose the first script
            # with a name matching the requested page name.
            current_page_info = next(
                filter(
                    # There seems to be this weird bug with mypy where it
                    # thinks that p can be None (which is impossible given the
                    # types of pages), so we add `p and` at the beginning of
                    # the predicate to circumvent this.
                    lambda p: p and (p["page_name"] == page_name),
                    pages.values(),
                ),
                None,
            )
            if current_page_info:
                page_hash = current_page_info["page_script_hash"]

        return {
            # We always run the main script in V2 as it's the common code
            "script_path": self._parent._main_script_path,
            "page_script_hash": page_hash or "",  # Default Hash
        }

    def get_pages(self):
        return self._pages or {
            self._parent._main_script_hash: {
                "page_script_hash": self._parent._main_script_hash,
                "page_name": "Main",
                "icon": "",
                "script_path": self._parent._main_script_path,
            }
        }

    def set_pages(self, pages):
        self._pages = pages


class PagesManager:
    _cached_pages: dict[str, dict[str, str]] | None = None
    _pages_cache_lock = threading.RLock()
    _on_pages_changed = Signal(doc="Emitted when the pages directory is changed")

    def __init__(self, main_script_path):
        self._main_script_path = main_script_path
        self._main_script_hash = calc_md5(main_script_path)
        self._version_manager = self._detect_multipage_mode()

    def get_main_page(self):
        return self._version_manager.get_main_page()

    def get_page_by_run(self, page_script_hash, page_name):
        return self._version_manager.get_page_by_run(page_script_hash, page_name)

    def get_pages(self):
        # Avoid taking the lock if the pages cache hasn't been invalidated.
        pages = self._cached_pages
        if pages is not None:
            return pages

        with self._pages_cache_lock:
            # The cache may have been repopulated while we were waiting to grab
            # the lock.
            if self._cached_pages is not None:
                return self._cached_pages

            pages = self._version_manager.get_pages()
            self._cached_pages = pages

            return pages

    def set_pages(self, pages):
        try:
            vm_pages = self._version_manager.set_pages(pages)
        except NotImplementedError:
            _LOGGER.warning(
                "We've detected a call to st.navigation in a script that has a pages directory."
            )
            self._version_manager = V2PagesManager(self)
            self.invalidate_pages_cache()
            vm_pages = self._version_manager.set_pages(pages)

        self._cached_pages = vm_pages

    @property
    def version(self):
        return self._version_manager.version

    @property
    def is_v2(self):
        return self._version_manager.version == MultipageAppsVersion.V2

    def invalidate_pages_cache(self) -> None:
        _LOGGER.debug("Pages directory changed")
        with self._pages_cache_lock:
            self._cached_pages = None

        self._on_pages_changed.send()

    def register_pages_changed_callback(
        self,
        callback: Callable[[str], None],
    ) -> Callable[[], None]:
        def disconnect():
            self._on_pages_changed.disconnect(callback)

        # weak=False so that we have control of when the pages changed
        # callback is deregistered.
        self._on_pages_changed.connect(callback, weak=False)

        return disconnect

    def _detect_multipage_mode(self) -> MultipageAppsVersion:
        """Detect the multipage version of the script.

        Returns
        -------
        MultipageAppsVersion
            The detected multipage version.
        """
        has_pages_dir = (Path(self._main_script_path).parent / "pages").is_dir()

        if has_pages_dir:
            return V1PagesManager(self)
        else:
            # With no pages directory, we assume the script runs with V2
            # This will work if st.navigation is or is not called in the
            # script because if it is not called, it's a single page script.
            return V2PagesManager(self)
