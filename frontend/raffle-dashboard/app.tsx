import { useId, useRef, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { Button as AriaButton, ComboBox, ListBox, Popover } from "react-aria-components";
import {
  RiAddLine, RiArrowLeftLine, RiArrowRightLine, RiArrowRightUpLine, RiCloseLine, RiCoupon3Line,
  RiDownload2Line, RiFileEditLine, RiGroupLine, RiInbox2Line,
  RiLogoutBoxRLine, RiStackLine, RiRefreshLine, RiBarChartBoxLine,
} from "@remixicon/react";
import { Counter } from "@/components/base/badges/counter";
import { Button, ButtonLink, type ButtonProps } from "@/components/base/buttons/button";
import { InputBase } from "@/components/base/input/input";
import { Label } from "@/components/base/input/label";
import { MENU_ITEMS_CONTAINER, MENU_POPOVER_SURFACE } from "@/components/base/dropdown/menu-styles";
import { SearchField } from "@/components/base/search-field/search-field";
import { Select, SelectItem } from "@/components/base/select/select";
import { Table, TableBody, TableCell, TableColumn, TableHeader, TableRow } from "@/components/base/table/table";
import { Tabs, Tab, TabList, TabPanel } from "@/components/base/tabs/tabs";
import { DirectionProvider } from "@/components/foundations/direction/direction";
import { ChevronDownSmall } from "@/components/foundations/icons/chevrons";
import { cx } from "@/utils/cx";
import "@/styles/globals.css";
import "./app.css";

type Account = { name: string; role: string; group: string; csrf: string };
type Action = { href: string; label: string; method?: "post"; secondary?: boolean; danger?: boolean; csrf?: string };
type Pager = { page: number; previous: string | null; next: string | null };
type State = "open" | "closed" | "drawn" | "cancelled";
type RaffleRow = {
  id: number; title: string; state: State; deadline: string; people: number; slots: number;
  own: number | null; created: boolean;
};
type Cell = string | number;
type View<Name extends string, Data> = { view: Name; title: string; data: Data; account: Account | null };
type Panel = View<"panel", {
  rows: RaffleRow[];
  filters: { query: string; status: string; view: string };
  tabs: { id: string; label: string; count: number; href: string }[];
  sync: { state: string; progress: string }; pager: Pager;
}>;
type SummaryRecord = {
  title: string; source: string; deadline: string; people: number; winners: string[];
} & ({ id: number; state: State } | { id: null; source_row: number; organizer: string });
type Rank = { rank: number; name: string; count: number };
type RankMatch = { name: string; entered: number; awarded: number };
type Summary = View<"summary", {
  stats: { total: number; live: number; archive: number; people: number; with_results: number; winners: number };
  rows: SummaryRecord[]; source: "" | "archive" | "live"; pager: Pager;
  rankings: { entered: Rank[]; awarded: Rank[]; matches: RankMatch[] };
  rank_limit: number; rank_query: string;
}>;
type Raffle = View<"raffle", {
  id: number; state: State; own: number | null; people: number; slots: number; deadline: string;
  actions: Action[]; pager: Pager;
  winners: { eligible: number; count: number; rows: Cell[][] } | null;
  management: {
    participants: Cell[][]; audit: Cell[][]; reminders: Cell[][];
    exports: { participants: Action; audit: Action };
  } | null;
}>;
type Draft = View<"draft", { draft: { title: string; code: string; slots: number; deadline: string } | null }>;
type NicknameOption = { id: string; label: string };
type Nicknames = View<"nicknames", {
  rules: { id: number; old: string; target: string }[]; message: string | null;
  old_options: NicknameOption[]; target_options: NicknameOption[];
}>;
type Notice = View<"notice", { message: string; action: Action | null; back: Action | null }>;
type Pairing = View<"pairing", { command: string }>;
type Model = Panel | Raffle | Draft | Summary | Nicknames | Notice | Pairing;

const states: Record<State, { text: string; color: string }> = {
  open: { text: "报名中", color: "bg-status-lime-background text-status-lime-text" },
  closed: { text: "待开奖", color: "bg-status-yellow-background text-status-yellow-text" },
  drawn: { text: "已开奖", color: "bg-status-blue-background text-status-blue-text" },
  cancelled: { text: "已取消", color: "bg-badge-neutral-background text-text-secondary" },
};

function ActionButton({ action, csrf, code, icon }: {
  action: Action; csrf?: string; code?: string; icon?: ButtonProps["leadingIcon"];
}) {
  const variant = action.danger ? "danger" : action.secondary ? "secondary" : "primary";
  if (action.method === "post") {
    const proof = action.csrf ?? csrf;
    return (
      <form action={action.href} method="post">
        {proof && <input type="hidden" name="csrf" value={proof} />}
        {code && <input type="hidden" name="code" value={code} />}
        <Button type="submit" variant={variant} leadingIcon={icon}>{action.label}</Button>
      </form>
    );
  }
  return <ButtonLink href={action.href} variant={variant} leadingIcon={icon}>{action.label}</ButtonLink>;
}

function Brand() {
  return (
    <a className="brand" href="/">
      <span className="brand-icon"><RiCoupon3Line aria-hidden /></span>
      <span>QQ 群抽奖</span>
    </a>
  );
}

function Frame({ account, view, children }: { account: Account | null; view: Model["view"]; children: ReactNode }) {
  if (!account) return <div className="entry-layout"><Brand /><main className="entry-content">{children}</main></div>;
  return (
    <div className="dashboard-shell">
      <aside className="sidebar">
        <Brand />
        <nav className="navigation" aria-label="抽奖管理">
          <ButtonLink href="/panel" variant="ghost" leadingIcon={RiStackLine}
            className="nav-link" aria-current={view === "panel" || view === "raffle" ? "page" : undefined}>抽奖记录</ButtonLink>
          <ButtonLink href="/panel/summary" variant="ghost" leadingIcon={RiBarChartBoxLine}
            className="nav-link" aria-current={view === "summary" || view === "nicknames" ? "page" : undefined}>抽奖总结</ButtonLink>
          <ButtonLink href="/panel/draft" variant="ghost" leadingIcon={RiFileEditLine}
            className="nav-link" aria-current={view === "draft" ? "page" : undefined}>待发布草稿</ButtonLink>
        </nav>
        <div className="sidebar-account">
          <div className="account-text"><b>{account.name}</b><span>{account.role}</span></div>
          <form method="post" action="/logout">
            <input type="hidden" name="csrf" value={account.csrf} />
            <Button variant="ghost" size="small" iconOnly leadingIcon={RiLogoutBoxRLine} aria-label="退出登录" type="submit" />
          </form>
        </div>
      </aside>
      <div className="workspace">
        <header className="workspace-bar">
          <span className="group-label"><RiGroupLine aria-hidden /> QQ 群 {account.group}</span>
          <span className="group-role"><b>{account.name}</b><span>{account.role}</span></span>
        </header>
        <main className="workspace-main">{children}</main>
      </div>
    </div>
  );
}

function Status({ value }: { value: State }) {
  const state = states[value];
  return <Counter className={cx("status-chip", state.color)}>{state.text}</Counter>;
}

function Pagination({ value }: { value: Pager }) {
  if (!value.previous && !value.next) return null;
  return (
    <nav className="pagination" aria-label="分页">
      {value.previous && <ButtonLink variant="secondary" size="small" href={value.previous}>上一页</ButtonLink>}
      <span>第 {value.page} 页</span>
      {value.next && <ButtonLink variant="secondary" size="small" href={value.next}>下一页</ButtonLink>}
    </nav>
  );
}

function Empty({ title, message }: { title: string; message?: string }) {
  return (
    <div className="empty-state">
      <span className="empty-icon"><RiInbox2Line aria-hidden /></span>
      <p className="text-body-medium">{title}</p>
      {message && <p className="text-body-regular text-text-secondary">{message}</p>}
    </div>
  );
}

function RaffleLink({ row }: { row: RaffleRow }) {
  return (
    <div className="record-name">
      <a href={`/panel/r/${row.id}`}>{row.title}<RiArrowRightUpLine aria-hidden /></a>
      <span>#{row.id}{row.created && " · 我发起的"}{row.own !== null && ` · 我的报名序号 ${row.own}`}</span>
    </div>
  );
}

function ActiveList({ rows }: { rows: RaffleRow[] }) {
  if (!rows.length) return <Empty title="没有匹配的抽奖" message="可以调整筛选，或准备一场新抽奖。" />;
  return (
    <>
      <Table aria-label="抽奖记录" containerClassName="desktop-table">
        <TableHeader>
          <TableColumn id="prize" isRowHeader>奖品</TableColumn>
          <TableColumn id="status">状态</TableColumn>
          <TableColumn id="people">报名／中奖名额</TableColumn>
          <TableColumn id="deadline">截止时间（北京时间）</TableColumn>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id} id={row.id}>
              <TableCell><RaffleLink row={row} /></TableCell>
              <TableCell><Status value={row.state} /></TableCell>
              <TableCell>{row.people} 人报名 <span className="soft">／抽 {row.slots} 人</span></TableCell>
              <TableCell>{row.deadline}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <ul className="mobile-records">
        {rows.map((row) => <li key={row.id}>
          <div className="record-top"><RaffleLink row={row} /><Status value={row.state} /></div>
          <div className="record-summary"><span>{row.people} 人报名 · 抽 {row.slots} 人</span><span>{row.deadline} 截止</span></div>
        </li>)}
      </ul>
    </>
  );
}

function PanelPage({ model }: { model: Panel }) {
  const { rows, filters, tabs, sync, pager } = model.data;
  const selection = filters.status || "all";
  return (
    <>
      <div className="page-heading">
        <h1>抽奖管理台</h1>
        <ActionButton action={{ href: "/panel/new", method: "post", label: "准备新抽奖" }} csrf={model.account?.csrf} icon={RiAddLine} />
      </div>
      <section aria-label="腾讯文档同步" className="sync-strip">
        <span className="sync-icon"><RiRefreshLine aria-hidden /></span>
        <div><strong>腾讯文档</strong><span>{sync.state}</span></div>
        <span className="sync-progress">{sync.progress}</span>
      </section>
      <section className="surface records-surface">
        <Tabs selectedKey={selection} keyboardActivation="manual" onSelectionChange={(key) => {
          const tab = tabs.find((item) => item.id === key);
          if (tab && tab.id !== selection) window.location.assign(tab.href);
        }}>
          <TabList aria-label="抽奖状态">
            {tabs.map((tab) => <Tab key={tab.id} id={tab.id} count={tab.count}>{tab.label}</Tab>)}
          </TabList>
          <TabPanel id={selection}>
            <form method="get" action="/panel" className="filter-form">
              <input type="hidden" name="status" value={filters.status} />
              <SearchField name="q" defaultValue={filters.query} aria-label="奖品或抽奖编号" className="query-filter"
                placeholder="搜索奖品或抽奖编号" clearLabel="清除搜索" shape="rounded" />
              <Select name="view" aria-label="查看范围" defaultSelectedKey={filters.view || "all"} className="view-filter">
                <SelectItem id="all">全部记录</SelectItem>
                <SelectItem id="mine">我发起的</SelectItem>
                <SelectItem id="joined">我报名的</SelectItem>
              </Select>
              <Button type="submit" variant="secondary">筛选</Button>
            </form>
            <ActiveList rows={rows} />
            <Pagination value={pager} />
          </TabPanel>
        </Tabs>
      </section>
    </>
  );
}

function RecordSection({ title, description, headings, rows, empty, action }: {
  title: string; description?: string; headings: string[]; rows: Cell[][]; empty: string; action?: Action;
}) {
  return (
    <section className="surface record-section">
      <div className="section-heading">
        <div><h2>{title}</h2>{description && <p>{description}</p>}</div>
        {action && <ActionButton action={action} icon={RiDownload2Line} />}
      </div>
      <Table aria-label={title} size="sm">
        <TableHeader>
          {headings.map((heading, index) => <TableColumn key={heading} isRowHeader={index === 0}>{heading}</TableColumn>)}
        </TableHeader>
        <TableBody renderEmptyState={() => <p className="table-empty">{empty}</p>}>
          {rows.map((row, index) => <TableRow key={index}>
            {row.map((cell, i) => <TableCell key={i}>{cell}</TableCell>)}
          </TableRow>)}
        </TableBody>
      </Table>
    </section>
  );
}

function SummaryName({ row }: { row: SummaryRecord }) {
  return <div className="record-name">
    {row.id === null
      ? <strong>{row.title}</strong>
      : <a href={`/panel/r/${row.id}`}>{row.title}<RiArrowRightUpLine aria-hidden /></a>}
    <span>{row.id === null
      ? [row.source, row.organizer && `发起人 ${row.organizer}`].filter(Boolean).join(" · ")
      : `#${row.id} · ${row.source}`}</span>
  </div>;
}

function SummaryResult({ row, showLabel }: { row: SummaryRecord; showLabel?: boolean }) {
  if (row.winners.length) {
    const preview = row.winners.slice(0, 6).join("、");
    return <span>{showLabel && <span className="soft">获奖人 </span>}{preview}{row.winners.length > 6 && ` 等 ${row.winners.length} 人`}</span>;
  }
  return row.id === null ? <span className="soft">未登记结果</span> : <Status value={row.state} />;
}

function Leaderboard({ title, rows, limit }: { title: string; rows: Rank[]; limit: number }) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  const visible = expanded ? rows : rows.slice(0, limit);
  return <section className="surface leaderboard" aria-label={title}>
    <div className="section-heading">
      <h2>{title}</h2>
      {rows.length > limit && <Button type="button" variant="secondary" size="small"
        aria-expanded={expanded} aria-controls={listId} onClick={() => setExpanded(!expanded)}>
        {expanded ? "收起榜单" : `查看全部 ${rows.length} 人`}
      </Button>}
    </div>
    {rows.length ? <ol id={listId} className="rank-list" aria-label={title}>
      {visible.map((row, index) => <li key={index}>
        <Counter className={cx("rank-place", row.rank <= 3 && "rank-place-prominent")}>
          <span className="sr-only">第 </span>{row.rank}<span className="sr-only"> 名</span>
        </Counter>
        <span className="rank-name">{row.name}</span>
        <strong>{row.count} 次</strong>
      </li>)}
    </ol> : <p className="table-empty">暂无记录</p>}
  </section>;
}

function NicknameSearch({ query, matches, source }: { query: string; matches: RankMatch[]; source: string }) {
  return <section className="surface nickname-search" id="nickname-search" aria-label="查群友统计">
    <div className="section-heading"><h2>查群友统计</h2></div>
    <form className="nickname-form" action="/panel/summary#nickname-search" method="get">
      {source && <input type="hidden" name="source" value={source} />}
      <SearchField name="nickname" defaultValue={query} aria-label="群友昵称"
        className="nickname-field" placeholder="输入完整或部分昵称" clearLabel="清空昵称" shape="rounded" />
      <Button type="submit" variant="secondary">搜索</Button>
      {query && <ButtonLink href={source ? `/panel/summary?source=${source}#nickname-search` : "/panel/summary#nickname-search"}
        variant="ghost">清除搜索</ButtonLink>}
    </form>
    {query && (matches.length ? <Table aria-label="昵称搜索结果" size="sm">
      <TableHeader>
        <TableColumn id="name" isRowHeader>昵称</TableColumn>
        <TableColumn id="entered">参与次数</TableColumn>
        <TableColumn id="awarded">中奖次数</TableColumn>
      </TableHeader>
      <TableBody>
        {matches.map((row, index) => <TableRow key={index}>
          <TableCell>{row.name}</TableCell>
          <TableCell>{row.entered} 次</TableCell>
          <TableCell>{row.awarded} 次</TableCell>
        </TableRow>)}
      </TableBody>
    </Table> : <p className="table-empty" role="status">未找到匹配的昵称</p>)}
  </section>;
}

function SummaryPage({ model }: { model: Summary }) {
  const { stats, rows, source, pager, rankings, rank_limit, rank_query } = model.data;
  const metrics = [
    ["抽奖总场次", stats.total], ["累计参与人次", stats.people],
    ["有结果的场次", stats.with_results], ["记录中奖人次", stats.winners],
  ] as const;
  const sections = [
    { key: "", label: "全部", count: stats.total },
    { key: "archive", label: "云文档历史", count: stats.archive },
    { key: "live", label: "机器人", count: stats.live },
  ];
  const rowKey = (row: SummaryRecord) => row.id ?? `${row.source}:${row.source_row}`;
  const sourceHref = (key: string) => {
    const params = new URLSearchParams();
    if (key) params.set("source", key);
    if (rank_query) params.set("nickname", rank_query);
    return "/panel/summary" + (params.size ? `?${params}` : "");
  };
  return <>
    <div className="page-heading">
      <div><h1>抽奖总结</h1><p className="summary-description">云文档历史和本群机器人抽奖 · 参与按各场人次累计</p></div>
      <div className="summary-heading-actions">
        {model.account?.role === "群管理" && <ButtonLink href="/panel/summary/nicknames" size="small">合并昵称</ButtonLink>}
        <ButtonLink variant="secondary" size="small" href="#summary-records">场次明细</ButtonLink>
      </div>
    </div>
    <dl className="summary-metrics">
      {metrics.map(([label, value]) => <div className="surface" key={label}>
        <dt>{label}</dt><dd>{value.toLocaleString("zh-CN")}</dd>
      </div>)}
    </dl>
    <NicknameSearch query={rank_query} matches={rankings.matches} source={source} />
    <section className="summary-rankings" aria-label="群友排行榜">
      <Leaderboard title="参与次数榜" rows={rankings.entered} limit={rank_limit} />
      <Leaderboard title="中奖次数榜" rows={rankings.awarded} limit={rank_limit} />
      <p className="summary-description rank-note">同次数并列 · 历史按登记昵称统计</p>
    </section>
    <section className="surface" id="summary-records">
      <div className="section-heading"><h2>场次明细</h2></div>
      <nav className="summary-filter" aria-label="记录来源">
        {sections.map(({ key, label, count }) => <ButtonLink key={key} size="small"
          href={sourceHref(key)}
          variant={key === source ? "ghost" : "secondary"} aria-current={key === source ? "page" : undefined}>
          {label} {count}
        </ButtonLink>)}
      </nav>
      {rows.length ? <>
        <div className="desktop-table summary-table">
          <Table aria-label="场次明细" size="sm">
            <TableHeader>
              <TableColumn id="title" isRowHeader>奖品与来源</TableColumn>
              <TableColumn id="deadline">截止时间</TableColumn>
              <TableColumn id="people">参与人次</TableColumn>
              <TableColumn id="result">结果</TableColumn>
            </TableHeader>
            <TableBody>
              {rows.map(row => <TableRow key={rowKey(row)}>
                <TableCell><SummaryName row={row} /></TableCell>
                <TableCell>{row.deadline || "未填写"}</TableCell>
                <TableCell>{row.people}</TableCell>
                <TableCell><SummaryResult row={row} /></TableCell>
              </TableRow>)}
            </TableBody>
          </Table>
        </div>
        <ul className="mobile-records" aria-label="场次明细">
          {rows.map(row => <li key={rowKey(row)}>
            <SummaryName row={row} />
            <div className="record-summary"><span>{row.people} 人次参与</span><span>{row.deadline || "未填写截止时间"}</span></div>
            <p className="summary-result"><SummaryResult row={row} showLabel /></p>
          </li>)}
        </ul>
      </> : <Empty title="暂无抽奖记录" />}
      <Pagination value={pager} />
    </section>
  </>;
}

type NicknamePickerProps = {
  label: string; field: string; options: NicknameOption[];
} & (
  { multiple: true; selected: string[]; onSelect: (keys: string[]) => void } |
  { multiple?: false; selected: NicknameOption | null; onSelect: (value: NicknameOption | null) => void }
);

function NicknamePicker(props: NicknamePickerProps) {
  const { label, field, options } = props;
  const [input, setInput] = useState("");
  const normalize = (value: string) => value.normalize("NFKC").trim().replace(/\s+/g, " ").toLowerCase();
  const common = {
    className: "merge-field", name: field, formValue: "key", menuTrigger: "focus",
    inputValue: input, defaultItems: options, isDisabled: !options.length, allowsEmptyCollection: true,
    defaultFilter: (name: string, query: string) => normalize(name).includes(normalize(query)),
  } as const;
  const content = <>
    <Label>{label}</Label>
    <InputBase placeholder={options.length ? "输入昵称搜索" : "暂无可选昵称"} autoComplete="off"
      onKeyDown={event => {
        if (props.multiple && event.key === "Escape") setInput("");
      }}
      trailingAddon={<AriaButton className="nickname-picker-toggle" aria-label={`展开${label}候选`}>
        <ChevronDownSmall aria-hidden />
      </AriaButton>} />
    <Popover className={cx(MENU_POPOVER_SURFACE, "nickname-picker-popover")} offset={4}>
      <ListBox className={MENU_ITEMS_CONTAINER} renderEmptyState={() => <p className="table-empty">未找到匹配昵称</p>}>
        {(option: NicknameOption) => <SelectItem id={option.id} textValue={option.label}>
          {option.label}
          {props.multiple && props.selected.includes(option.id) && <span aria-hidden className="nickname-option-check">✓</span>}
        </SelectItem>}
      </ListBox>
    </Popover>
  </>;
  if (props.multiple) {
    return <ComboBox<NicknameOption, "multiple"> {...common} selectionMode="multiple"
      value={props.selected} onInputChange={setInput} onChange={keys => props.onSelect(keys.map(String))}>
      {content}
    </ComboBox>;
  }
  return <ComboBox {...common} selectedKey={props.selected?.id ?? null}
    onInputChange={next => {
      setInput(next);
      if (next !== props.selected?.label) props.onSelect(null);
    }}
    onSelectionChange={key => {
      const option = options.find(value => value.id === key) ?? null;
      props.onSelect(option);
      setInput(option?.label ?? "");
    }}>
    {content}
  </ComboBox>;
}

function NicknamesPage({ model }: { model: Nicknames }) {
  const { rules, message, old_options, target_options } = model.data;
  const csrf = model.account?.csrf;
  const [old, selectOld] = useState<string[]>([]);
  const [target, selectTarget] = useState<NicknameOption | null>(null);
  const same = target !== null && old.includes(target.id);
  const chosen = old.map(id => old_options.find(option => option.id === id)).filter(option => option !== undefined);
  return <>
    <Back href="/panel/summary" label="抽奖总结" />
    <div className="page-heading">
      <div><h1>合并历史昵称</h1>
        <p className="summary-description">选中一个或多个旧昵称，统一归入同一个昵称。</p>
      </div>
    </div>
    {message && <p className="nickname-feedback" role="status">{message}</p>}
    <section className="surface nickname-search" aria-label="添加合并">
      <form method="post" action="/panel/summary/nicknames" className="merge-form">
        <input type="hidden" name="csrf" value={csrf} />
        <NicknamePicker multiple label="旧昵称" field="old_choice" options={old_options} selected={old} onSelect={selectOld} />
        <NicknamePicker label="归入昵称" field="target_choice" options={target_options} selected={target} onSelect={selectTarget} />
        <Button type="submit" disabled={!old.length || !target || same}>合并昵称</Button>
        {!!chosen.length && <ul className="nickname-selection" aria-label="已选旧昵称">
          {chosen.map(option => <li key={option.id}>
            <Button size="xs" type="button" variant="ghost" trailingIcon={RiCloseLine}
              aria-label={`移除 ${option.label}`} onClick={() => selectOld(ids => ids.filter(id => id !== option.id))}>
              <span title={option.label}>{option.label}</span>
            </Button>
          </li>)}
        </ul>}
        {same && <p className="merge-form-hint" role="status">归入昵称与所选旧昵称重复，请换一个。</p>}
      </form>
    </section>
    <section className="surface" aria-label="已有合并">
      <div className="section-heading"><h2>已有合并</h2></div>
      {rules.length ? <ul className="merge-rules">
        {rules.map(rule => <li key={rule.id}>
          <div><span>{rule.old}</span><RiArrowRightLine aria-label="归入" /><strong>{rule.target}</strong></div>
          <form method="post" action={`/panel/summary/nicknames/${rule.id}/undo`}>
            <input type="hidden" name="csrf" value={csrf} />
            <Button type="submit" variant="secondary" size="small" aria-label={`撤销合并 ${rule.old}`}>撤销</Button>
          </form>
        </li>)}
      </ul> : <p className="table-empty">暂无昵称合并</p>}
    </section>
  </>;
}

function Back({ href = "/panel", label = "抽奖记录" }: { href?: string; label?: string }) {
  return <ButtonLink variant="ghost" size="small" href={href} leadingIcon={RiArrowLeftLine} className="back">{label}</ButtonLink>;
}

function RafflePage({ model }: { model: Raffle }) {
  const { id, state, own, people, slots, deadline, actions, winners, management, pager } = model.data;
  return (
    <>
      <Back />
      <section className="surface raffle-summary">
        <div className="summary-heading">
          <div><div className="record-id">抽奖 #{id}</div><h1>{model.title}</h1></div>
          <Status value={state} />
        </div>
        <dl className="facts">
          <div><dt>报名人数</dt><dd>{people}</dd></div>
          <div><dt>计划中奖</dt><dd>{slots}</dd></div>
          <div><dt>截止时间（北京时间）</dt><dd>{deadline}</dd></div>
          {own !== null && <div><dt>我的报名序号</dt><dd>{own}</dd></div>}
        </dl>
        {!!actions.length && <div className="actions">
          {actions.map((action) => <ActionButton key={action.href} action={action} csrf={model.account?.csrf} />)}
        </div>}
      </section>
      {winners && <RecordSection title="开奖结果"
        description={`共 ${winners.eligible} 人报名，随机抽中 ${winners.count} 人。`}
        headings={["抽中序号", "中奖昵称"]} rows={winners.rows} empty="本页没有中奖记录。" />}
      {management && <>
        <RecordSection title="报名名单" headings={["序号", "昵称"]} rows={management.participants}
          empty="本页没有报名记录。" action={management.exports.participants} />
        <RecordSection title="操作流水" headings={["时间", "事件", "昵称", "详情"]} rows={management.audit}
          empty="本页没有操作记录。" action={management.exports.audit} />
        <RecordSection title="QQ 提醒与群消息" headings={["提醒", "预定时间", "发送状态"]}
          rows={management.reminders} empty="这场抽奖没有提醒记录。" />
      </>}
      <Pagination value={pager} />
    </>
  );
}

function DraftPage({ model }: { model: Draft }) {
  const { draft } = model.data;
  return (
    <section className="surface form-card">
      <h1>待发布草稿</h1>
      {draft ? <>
        <h2>{draft.title}</h2>
        <dl className="facts"><div><dt>计划中奖</dt><dd>{draft.slots} 人</dd></div>
          <div><dt>截止时间（北京时间）</dt><dd>{draft.deadline}</dd></div></dl>
        <p>发布后，机器人会在原 QQ 群发出报名消息。</p>
        <ActionButton action={{ href: "/panel/draft", method: "post", label: "发布到 QQ 群" }} csrf={model.account?.csrf} code={draft.code} />
      </> : <>
        <p>没有可发布的草稿。</p>
        <ActionButton action={{ href: "/panel/new", method: "post", label: "准备新抽奖" }} csrf={model.account?.csrf} icon={RiAddLine} />
      </>}
    </section>
  );
}

function NoticePage({ model }: { model: Notice }) {
  const { message, action, back } = model.data;
  return (
    <>
      {back && <Back href={back.href} label={back.label} />}
      <section className="surface form-card">
        <h1>{model.title}</h1><p>{message}</p>
        {action && <ActionButton action={action} csrf={model.account?.csrf} />}
      </section>
    </>
  );
}

function PairingPage({ model }: { model: Pairing }) {
  const input = useRef<HTMLInputElement>(null);
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(model.data.command);
      setCopied(true);
    } catch {
      input.current?.focus();
      input.current?.select();
    }
  }
  return (
    <section className="surface form-card">
      <h1>{model.title}</h1>
      <p>由领取链接的 QQ 号在原群发送下面的指令，确认后回到此页。</p>
      <InputBase ref={input} readOnly value={model.data.command} aria-label="QQ 访问确认指令" dir="ltr" />
      <div className="actions">
        <Button onClick={copy} variant="secondary">{copied ? "已复制" : "复制指令"}</Button>
        <ButtonLink href="/panel">我已确认，进入管理台</ButtonLink>
      </div>
    </section>
  );
}

function App({ model }: { model: Model }) {
  const views = () => {
    switch (model.view) {
      case "panel": return <PanelPage model={model} />;
      case "raffle": return <RafflePage model={model} />;
      case "summary": return <SummaryPage model={model} />;
      case "nicknames": return <NicknamesPage model={model} />;
      case "draft": return <DraftPage model={model} />;
      case "notice": return <NoticePage model={model} />;
      case "pairing": return <PairingPage model={model} />;
    }
  };
  return <DirectionProvider locale="zh-CN"><Frame account={model.account} view={model.view}>{views()}</Frame></DirectionProvider>;
}

const root = document.getElementById("raffle-app");
const data = document.getElementById("raffle-model");
if (root && data?.textContent) {
  const model = JSON.parse(data.textContent) as Model;
  createRoot(root).render(<App model={model} />);
}
