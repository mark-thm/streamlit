/**
 * Copyright (c) Streamlit Inc. (2018-2022) Snowflake Inc. (2022-2024)
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import {
  HostCommunicationManager,
  IAppPage,
  NewSession,
  Navigation,
  PagesChanged,
  PageNotFound,
} from "@streamlit/lib"
import { SegmentMetricsManager } from "@streamlit/app/src/SegmentMetricsManager"

export interface AppNavigationState {
  hideSidebarNav: boolean
  appPages: IAppPage[]
  currentPageScriptHash: string
  navPageSections: Map<string, IAppPage[]>
}

type AtLeastOne<T, U = { [K in keyof T]: Pick<T, K> }> = Partial<T> &
  U[keyof U]

export type MaybeStateUpdate =
  | [AtLeastOne<AppNavigationState>, () => void]
  | undefined
export type PageUrlUpdateCallback = (
  mainPageName: string,
  newPageName: string,
  isViewingMainPage: boolean
) => void
export type PageNotFoundCallback = (pageName?: string) => void

export class AppNavigationV1 {
  appPages: IAppPage[]

  currentPageScriptHash: string | null

  hideSidebarNav: boolean | null

  parent: AppNavigation

  constructor(parent: AppNavigation) {
    this.parent = parent
    this.appPages = []
    this.currentPageScriptHash = null
    this.hideSidebarNav = null
  }

  handleNewSession(newSession: NewSession): MaybeStateUpdate {
    this.appPages = newSession.appPages
    this.currentPageScriptHash = newSession.pageScriptHash
    this.hideSidebarNav = newSession.config?.hideSidebarNav ?? false

    // mainPage must be a string as we're guaranteed at this point that
    // newSessionProto.appPages is nonempty and has a truthy pageName.
    // Otherwise, we'd either have no main script or a nameless main script,
    // neither of which can happen.
    const mainPage = this.appPages[0] as IAppPage
    const mainPageName = mainPage.pageName ?? ""
    // We're similarly guaranteed that newPageName will be found / truthy
    // here.
    const newPageName =
      this.appPages.find(
        page => page.pageScriptHash === this.currentPageScriptHash
      )?.pageName ?? ""

    const isViewingMainPage =
      mainPage.pageScriptHash === this.currentPageScriptHash
    this.parent.onUpdatePageUrl(mainPageName, newPageName, isViewingMainPage)

    document.title = `${newPageName ?? ""} · Streamlit`

    this.parent.metricsMgr.enqueue("updateReport", {
      numPages: this.appPages.length,
      isMainPage: isViewingMainPage,
    })

    return [
      {
        hideSidebarNav: this.hideSidebarNav,
        appPages: this.appPages,
        currentPageScriptHash: this.currentPageScriptHash,
      },
      () => {
        this.parent.hostCommunicationMgr.sendMessageToHost({
          type: "SET_APP_PAGES",
          appPages: this.appPages,
        })

        this.parent.hostCommunicationMgr.sendMessageToHost({
          type: "SET_CURRENT_PAGE_NAME",
          currentPageName: isViewingMainPage ? "" : newPageName,
          currentPageScriptHash: this.currentPageScriptHash as string,
        })
      },
    ]
  }

  handlePagesChanged(pagesChangedMsg: PagesChanged): MaybeStateUpdate {
    const { appPages } = pagesChangedMsg
    return [
      { appPages },
      () => {
        this.parent.hostCommunicationMgr.sendMessageToHost({
          type: "SET_APP_PAGES",
          appPages,
        })
      },
    ]
  }

  handlePageNotFound(pageNotFound: PageNotFound): MaybeStateUpdate {
    const { pageName } = pageNotFound
    this.parent.onPageNotFound(pageName)
    const currentPageScriptHash = this.appPages[0]?.pageScriptHash ?? ""

    return [
      { currentPageScriptHash },
      () => {
        this.parent.hostCommunicationMgr.sendMessageToHost({
          type: "SET_CURRENT_PAGE_NAME",
          currentPageName: "",
          currentPageScriptHash,
        })
      },
    ]
  }
}

export class AppNavigationV2 {
  private readonly parent: AppNavigation

  constructor(parent: AppNavigation) {
    this.parent = parent
  }

  handleNewSession(_newSession: NewSession): MaybeStateUpdate {
    // We do not know the page name, so use an empty string version
    document.title = " · Streamlit"

    return undefined
  }

  handleNavigation(navigationMsg: Navigation): MaybeStateUpdate {
    const { sections, position } = navigationMsg
    const navPageSections = new Map()
    for (const section of sections) {
      navPageSections.set(section.header || "", section.appPages)
    }

    const appPages = sections.flatMap(section => section.appPages || [])
    const hideSidebarNav = position == "hidden"

    const currentPage = appPages.find(
      p => p.pageScriptHash === navigationMsg.pageScriptHash
    ) as IAppPage
    const currentPageScriptHash = currentPage.pageScriptHash as string
    const currentPageName = currentPage.isDefault
      ? ""
      : (currentPage.pageName as string)

    this.parent.metricsMgr.enqueue("updateReport", {
      numPages: appPages.length,
      isMainPage: currentPage.isDefault,
      // TODO(kmcgrady): Add metric for v2 or v1
    })

    this.parent.onUpdatePageUrl(
      "",
      currentPageName,
      currentPage.isDefault ?? false
    )

    return [
      {
        appPages: [],
        navPageSections,
        hideSidebarNav,
        currentPageScriptHash,
      },
      () => {
        this.parent.hostCommunicationMgr.sendMessageToHost({
          type: "SET_APP_PAGES",
          appPages,
        })

        this.parent.hostCommunicationMgr.sendMessageToHost({
          type: "SET_CURRENT_PAGE_NAME",
          currentPageName: currentPageName,
          currentPageScriptHash,
        })
      },
    ]
  }

  handlePagesChanged(_pagesChangedMsg: PagesChanged): MaybeStateUpdate {
    return undefined
  }

  handlePageNotFound(_pageNotFound: PageNotFound): MaybeStateUpdate {
    return undefined
  }
}

export class AppNavigation {
  readonly hostCommunicationMgr: HostCommunicationManager

  readonly metricsMgr: SegmentMetricsManager

  readonly onUpdatePageUrl: PageUrlUpdateCallback

  readonly onPageNotFound: PageNotFoundCallback

  private versionManager: AppNavigationV2 | AppNavigationV1 | null

  constructor(
    hostCommunicationMgr: HostCommunicationManager,
    metricsMgr: SegmentMetricsManager,
    onUpdatePageUrl: PageUrlUpdateCallback,
    onPageNotFound: PageNotFoundCallback
  ) {
    this.hostCommunicationMgr = hostCommunicationMgr
    this.metricsMgr = metricsMgr
    this.onUpdatePageUrl = onUpdatePageUrl
    this.onPageNotFound = onPageNotFound
    this.versionManager = null
  }

  handleNewSession(newSession: NewSession): MaybeStateUpdate {
    if (newSession.appPages.length > 1 && this.versionManager === null) {
      // We assume it's V1 based on our understanding.
      this.versionManager = new AppNavigationV1(this)
    }

    return this.versionManager?.handleNewSession(newSession)
  }

  handlePagesChanged(pagesChangedMsg: PagesChanged): MaybeStateUpdate {
    return this.versionManager?.handlePagesChanged(pagesChangedMsg)
  }

  handleNavigation(navigation: Navigation): MaybeStateUpdate {
    if (!(this.versionManager instanceof AppNavigationV2)) {
      this.versionManager = new AppNavigationV2(this)
    }

    return this.versionManager.handleNavigation(navigation)
  }

  handlePageNotFound(pageNotFoundMsg: PageNotFound): MaybeStateUpdate {
    return this.versionManager?.handlePageNotFound(pageNotFoundMsg)
  }
}
