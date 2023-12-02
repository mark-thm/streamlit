# Copyright (c) Streamlit Inc. (2018-2022) Snowflake Inc. (2022)
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

from typing import NoReturn

import streamlit as st
from streamlit import source_util
from streamlit.deprecation_util import make_deprecated_name_warning
from streamlit.errors import StreamlitAPIException
from streamlit.logger import get_logger
from streamlit.runtime.metrics_util import gather_metrics
from streamlit.runtime.scriptrunner import RerunData, RerunException, get_script_run_ctx

_LOGGER = get_logger(__name__)


@gather_metrics("stop")
def stop() -> NoReturn:  # type: ignore[misc]
    """Stops execution immediately.

    Streamlit will not run any statements after `st.stop()`.
    We recommend rendering a message to explain why the script has stopped.

    Example
    -------
    >>> import streamlit as st
    >>>
    >>> name = st.text_input('Name')
    >>> if not name:
    >>>   st.warning('Please input a name.')
    >>>   st.stop()
    >>> st.success('Thank you for inputting a name.')

    """
    ctx = get_script_run_ctx()

    if ctx and ctx.script_requests:
        ctx.script_requests.request_stop()
        # Force a yield point so the runner can stop
        st.empty()


@gather_metrics("rerun")
def rerun() -> NoReturn:  # type: ignore[misc]
    """Rerun the script immediately.

    When `st.rerun()` is called, the script is halted - no more statements will
    be run, and the script will be queued to re-run from the top.
    """

    ctx = get_script_run_ctx()

    if ctx and ctx.script_requests:
        query_string = ctx.query_string
        page_script_hash = ctx.page_script_hash

        ctx.script_requests.request_rerun(
            RerunData(
                query_string=query_string,
                page_script_hash=page_script_hash,
            )
        )
        # Force a yield point so the runner can do the rerun
        st.empty()


@gather_metrics("experimental_rerun")
def experimental_rerun() -> NoReturn:
    """Rerun the script immediately.

    When `st.experimental_rerun()` is called, the script is halted - no
    more statements will be run, and the script will be queued to re-run
    from the top.
    """
    msg = make_deprecated_name_warning("experimental_rerun", "rerun", "2024-04-01")
    # Log warning before the rerun, or else it would be interrupted
    # by the rerun. We do not send a frontend warning because it wouldn't
    # be seen.
    _LOGGER.warning(msg)
    rerun()


@gather_metrics("switch_page")
def switch_page(page_path: str) -> NoReturn:
    """Switch the current programmatically page in a multi-page app.

    When `st.switch_page()` is called with a page_path, the current page script is halted
    and the requested page script will be queued to run from the top.

    Parameters
    ----------
    page_path: str
        The label of the page to switch to, or the page's file path relative to the main script.
    """

    if page_path.startswith("."):
        requested_page_path = page_path.replace("./", "/")
    else:
        requested_page_path = page_path

    # option to pass in the label as the page_path
    requested_page_name = (
        requested_page_path.lower().replace("_", " ").replace("-", " ")
    )

    ctx = get_script_run_ctx()

    if ctx and ctx.script_requests:
        query_string = ctx.query_string

    # TODO: Figure out best way to retrieve app's page data
    # - streamlit.source_util.get_pages requires the main script path (used by streamlit extras)
    # - is using the global _cached_pages hacky/problematic ?

    mpa_pages = source_util._cached_pages
    page_data = mpa_pages.values()  # type: ignore[union-attr]
    page_names = [page["page_name"].replace("_", " ") for page in page_data]

    for page in page_data:
        page_name = page["page_name"].lower().replace("_", " ")
        path = page["script_path"]
        if requested_page_path in path or requested_page_name == page_name:
            raise RerunException(
                RerunData(
                    query_string=query_string,
                    page_script_hash=page["page_script_hash"],
                )
            )

    # TODO: Update warning message once page_path input options confirmed.
    raise StreamlitAPIException(
        f"Could not find page_path: {page_path}.  Must be the file path relative to the main script, or one of the following page names: {', '.join(page_names)}."
    )
