"""CLI 界面 - 命令行交互入口"""

import asyncio
import logging
import sys
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich import box

from dj_set_curator.curator import DJSetCurator
from dj_set_curator.mcp_client import CloudMusicMCPClient
from dj_set_curator.config import get_mcp_server_command, get_config_path, save_config, load_config

app = typer.Typer(
    name="dj-curator",
    help="基于锚点歌曲的智能 DJ 选曲工具",
    no_args_is_help=True,
)
console = Console()


def _show_config_help():
    """显示配置文件帮助信息"""
    config_path = get_config_path()
    console.print(
        Panel(
            f"[bold yellow]MCP Server 未找到[/bold yellow]\n\n"
            f"默认命令 [dim]cloud-music-mcp[/dim] 不在 PATH 中。\n"
            f"请通过以下任一方式配置正确的 MCP Server 路径:\n\n"
            f"[bold]1. 配置文件（推荐）[/bold]\n"
            f"   编辑 [cyan]{config_path}[/cyan]:\n"
            f"   [dim]mcp_server_command: /path/to/mcp-server-wrapper.sh[/dim]\n\n"
            f"[bold]2. 环境变量[/bold]\n"
            f"   [dim]export DJ_CURATOR_MCP_SERVER=/path/to/mcp-server-wrapper.sh[/dim]\n\n"
            f"[bold]3. 命令行参数[/bold]\n"
            f"   每次执行时添加 [dim]--server /path/to/mcp-server-wrapper.sh[/dim]\n\n"
            f"也可以使用 [cyan]dj-curator config --mcp-server /path/to/server[/cyan] 一键设置。",
            title="⚠️ 配置Required",
            border_style="yellow",
        )
    )


async def _check_login(mcp: CloudMusicMCPClient) -> bool:
    """检查 MCP Server 登录状态"""
    status = await mcp.check_status()
    if not status.get("logged_in", False):
        console.print(
            Panel(
                "[bold red]未登录网易云音乐[/bold red]\n"
                "请先在 cloud-music-mcp-extended 中执行扫码登录:\n"
                "  cd ../cloud-music-mcp-extended\n"
                "  python3 scripts/login.py",
                title="⚠️ 登录Required",
                border_style="red",
            )
        )
        return False
    return True


@app.command()
def create(
    anchor: List[str] = typer.Option(
        ..., "--anchor", "-a",
        help="锚点歌曲（可多次指定，支持 'Artist - Song' 格式或网易云 ID）",
    ),
    name: Optional[str] = typer.Option(
        None, "--name", "-n",
        help="输出歌单名称（可选，不传则自动生成）",
    ),
    count: int = typer.Option(
        20, "--count", "-c",
        help="目标歌曲数量",
        min=1, max=100,
    ),
    bpm_tolerance: float = typer.Option(
        5.0, "--bpm-tol",
        help="BPM 容差范围",
    ),
    diversity: float = typer.Option(
        0.8, "--diversity", "-d",
        help="多样性比例（0-1），越高歌单风格越多样",
        min=0.0, max=1.0,
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="显示详细的选曲列表和评分",
    ),
    server_command: Optional[str] = typer.Option(
        None, "--server",
        help="MCP Server 命令（默认按优先级：环境变量 > 配置文件 > cloud-music-mcp）",
    ),
    expand: bool = typer.Option(
        True, "--expand/--no-expand",
        help="候选不足时启用级联扩展（用候选歌曲作为二级锚点继续搜索）",
    ),
    arrange_mode: str = typer.Option(
        "flat", "--arrange", "-r",
        help="能量曲线编排模式: flat(均匀)/warm-up(渐进)/peak-mid(中段高潮)/rollercoaster(起伏)/climax-end(结尾高潮)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="预览模式，只显示候选和预测选曲，不创建歌单",
    ),
):
    """基于锚点歌曲创建 DJ Set 歌单"""

    async def run():
        mcp_cmd = get_mcp_server_command(server_command)
        mcp_client = CloudMusicMCPClient(server_command=mcp_cmd)
        try:
            async with mcp_client as mcp:
                # 检查登录
                if not await _check_login(mcp):
                    raise typer.Exit(1)

                # 显示锚点分析
                console.print(f"\n[bold cyan]🎵 锚点歌曲分析:[/bold cyan]")
                for i, a in enumerate(anchor, 1):
                    console.print(f"  {i}. [dim]{a}[/dim]")

                # 构建筛选配置
                filter_config = {
                    "bpm_tolerance": bpm_tolerance,
                }

                curator = DJSetCurator(mcp_client=mcp, filter_config=filter_config)

                # 执行选曲
                with console.status("[bold green]正在构建歌单..."):
                    try:
                        result = await curator.build_playlist(
                            anchor_queries=list(anchor),
                            playlist_name=name,
                            target_count=count,
                            diversity_ratio=diversity,
                            enable_expand=expand,
                            arrange_mode=arrange_mode,
                            dry_run=dry_run,
                        )
                    except ValueError as e:
                        console.print(f"[bold red]输入错误: {e}[/bold red]")
                        raise typer.Exit(1)
                    except RuntimeError as e:
                        console.print(f"[bold red]运行错误: {e}[/bold red]")
                        raise typer.Exit(1)

                # 展示结果
                anchor_count = result['stats'].get('anchor_count', len(result['anchors']))
                selected_count = result['stats'].get('selected_count', result['stats']['filtered_count'] - anchor_count)

                if dry_run:
                    console.print(f"\n[bold yellow]🔍 预览模式 - 未创建歌单[/bold yellow]")
                    console.print(
                        Panel(
                            f"[bold]{result['playlist_name']}[/bold]\n"
                            f"  锚点: {', '.join(a.name for a in result['anchors'])}\n"
                            f"  候选池: {result['stats']['total_candidates']} 首\n"
                            f"  总入选: {result['stats']['filtered_count']} 首（锚点 {anchor_count} 首 + 推荐 {selected_count} 首）\n"
                            f"  平均评分: {result['stats']['avg_score']}",
                            title="📁 预览信息",
                            border_style="yellow",
                        )
                    )
                else:
                    console.print(f"\n[bold green]✅ 歌单构建完成![/bold green]")
                    console.print(
                        Panel(
                            f"[bold]{result['playlist_name']}[/bold]\n"
                            f"  ID: {result['playlist_id']}\n"
                            f"  锚点: {', '.join(a.name for a in result['anchors'])}\n"
                            f"  候选池: {result['stats']['total_candidates']} 首\n"
                            f"  总入选: {result['stats']['filtered_count']} 首（锚点 {anchor_count} 首 + 推荐 {selected_count} 首）\n"
                            f"  平均评分: {result['stats']['avg_score']}",
                            title="📁 歌单信息",
                            border_style="green",
                        )
                    )

                # 详细列表
                if verbose or dry_run:
                    table = Table(
                        title=f"选曲列表 - {result['playlist_name']}",
                        box=box.ROUNDED,
                    )
                    table.add_column("#", style="cyan", justify="right", width=4)
                    table.add_column("歌曲", style="magenta")
                    table.add_column("艺术家", style="green")
                    table.add_column("评分", style="yellow", justify="right", width=6)
                    table.add_column("匹配原因", style="dim")

                    # 先展示锚点歌曲
                    for i, a in enumerate(result["anchors"], 1):
                        table.add_row(
                            str(i),
                            a.name,
                            a.artist,
                            "—",
                            "[bold cyan]锚点[/bold cyan]",
                        )

                    # 再展示推荐歌曲
                    for i, s in enumerate(result["selected_songs"], 1 + anchor_count):
                        reasons = ", ".join(s.match_reasons) if s.match_reasons else "相似推荐"
                        table.add_row(
                            str(i),
                            s.song.name,
                            s.song.artist,
                            f"{s.score:.0f}",
                            reasons,
                        )
                    console.print(table)

                # 状态摘要
                if verbose:
                    status = curator.get_status()
                    console.print(f"\n[dim]最后状态: {status['stage']} - {status['message']} (进度: {status['progress']}%)[/dim]")

                return result
        except FileNotFoundError:
            _show_config_help()
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[bold red]连接 MCP Server 失败: {e}[/bold red]")
            _show_config_help()
            raise typer.Exit(1)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\n[dim]已取消[/dim]")
        raise typer.Exit(130)


@app.command()
def version():
    """显示版本信息"""
    from dj_set_curator import __version__
    console.print(f"dj-set-curator [bold cyan]{__version__}[/bold cyan]")


@app.command()
def config(
    mcp_server: Optional[str] = typer.Option(
        None, "--mcp-server",
        help="设置 MCP Server 命令路径（持久化到配置文件）",
    ),
    show: bool = typer.Option(
        False, "--show",
        help="显示当前配置",
    ),
):
    """查看和修改配置"""
    config_path = get_config_path()

    if show or (mcp_server is None):
        cfg = load_config()
        console.print(f"[bold]配置文件路径:[/bold] [cyan]{config_path}[/cyan]")
        if cfg:
            console.print("[bold]当前配置:[/bold]")
            for k, v in cfg.items():
                console.print(f"  {k}: [green]{v}[/green]")
        else:
            console.print("[dim]（暂无配置，使用默认值）[/dim]")
        console.print(
            f"\n[dim]提示: 使用 [cyan]dj-curator config --mcp-server /path/to/server[/cyan] 设置 MCP Server 路径[/dim]"
        )
        return

    if mcp_server is not None:
        cfg = load_config()
        cfg["mcp_server_command"] = mcp_server
        if save_config(cfg):
            console.print(f"[bold green]✅ 配置已保存[/bold green]")
            console.print(f"   mcp_server_command: [cyan]{mcp_server}[/cyan]")
            console.print(f"   配置文件: [dim]{config_path}[/dim]")
        else:
            console.print(f"[bold red]❌ 配置保存失败[/bold red]")
            raise typer.Exit(1)


def main():
    """CLI 入口"""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app()
