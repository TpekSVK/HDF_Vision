from app.services.jetson_stats_service import JetsonStatsService


def test_parse_tegrastats_line_extracts_expected_values() -> None:
    line = (
        "RAM 3215/7760MB (lfb 132x4MB) SWAP 0/3880MB "
        "CPU [12%@1420,10%@1420,8%@1420,5%@1420] EMC_FREQ 0% "
        "GR3D_FREQ 18% PLL@32C CPU@45C GPU@44C"
    )

    stats = JetsonStatsService.parse_tegrastats_line(line)

    assert stats is not None
    assert stats.cpu_percent == 9
    assert stats.gpu_percent == 18
    assert round(stats.ram_used_gb, 2) == 3.14
    assert round(stats.ram_total_gb, 2) == 7.58
    assert stats.temp_c == 45.0


def test_parse_tegrastats_line_returns_none_for_invalid_input() -> None:
    assert JetsonStatsService.parse_tegrastats_line("not tegrastats") is None
