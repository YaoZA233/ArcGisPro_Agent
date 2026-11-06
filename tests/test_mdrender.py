import unittest

from arcgis_agent.mdrender import render_markdown, supports_color, _display_width


class MarkdownRenderTests(unittest.TestCase):
    def test_headings_lose_hash_markers_in_plain_mode(self):
        out = render_markdown("# 标题一\n## 标题二", color=False, width=60)
        self.assertNotIn("#", out)
        self.assertIn("标题一", out)
        self.assertIn("标题二", out)

    def test_headings_are_styled_in_color_mode(self):
        out = render_markdown("# Title", color=True, width=60)
        self.assertIn("\033[", out)  # carries ANSI styling
        self.assertNotIn("# Title", out)
        self.assertIn("Title", out)

    def test_inline_markers_are_removed_in_plain_mode(self):
        out = render_markdown("这是 **粗体** 和 `代码` 与 *斜体*", color=False, width=80)
        self.assertNotIn("**", out)
        self.assertNotIn("`", out)
        self.assertIn("粗体", out)
        self.assertIn("代码", out)
        self.assertIn("斜体", out)

    def test_link_becomes_text_and_url_in_plain_mode(self):
        out = render_markdown("见 [Esri 文档](https://example.com)", color=False, width=80)
        self.assertIn("Esri 文档", out)
        self.assertIn("https://example.com", out)
        self.assertNotIn("](", out)

    def test_code_block_content_preserved(self):
        md = "```python\nimport arcpy\narcpy.sa.Fill('dem')\n```"
        plain = render_markdown(md, color=False, width=60)
        self.assertIn("import arcpy", plain)
        self.assertIn("arcpy.sa.Fill('dem')", plain)
        colored = render_markdown(md, color=True, width=60)
        self.assertIn("import arcpy", colored)

    def test_lists_use_bullet_marker(self):
        out = render_markdown("- 第一项\n- 第二项", color=False, width=60)
        self.assertIn("•", out)
        self.assertIn("第一项", out)
        self.assertNotIn("- 第一项", out)

    def test_table_is_aligned_with_cjk_width(self):
        md = "| 指标 | 值 |\n| --- | --- |\n| 流域面积 | 1234.5 |"
        out = render_markdown(md, color=False, width=60)
        rows = [line for line in out.split("\n") if line.startswith("|")]
        self.assertEqual(len(rows), 2)
        # Both data rows must share the same display width for correct alignment.
        self.assertEqual(_display_width(rows[0]), _display_width(rows[1]))

    def test_chinese_paragraphs_and_long_lines_wrap_without_loss(self):
        text = "中文" * 35
        out = render_markdown(text + "\n\n第二段", color=False, width=20)
        self.assertEqual("".join(out.split("\n\n")[0].splitlines()), text)
        self.assertIn("\n\n第二段", out)
        self.assertTrue(all(_display_width(line) <= 20 for line in out.splitlines()))

    def test_narrow_table_wraps_instead_of_truncating(self):
        value = "长路径内容" * 8
        out = render_markdown("| 名称 | 值 |\n| --- | --- |\n| 路径 | " + value + " |", color=False, width=24)
        self.assertNotIn("…", out)
        self.assertEqual("".join(line.split("|")[2].strip() for line in out.splitlines() if line.startswith("|") and "名称" not in line), value)

    def test_table_escaped_pipe_is_not_a_column(self):
        out = render_markdown("| A | B |\n| --- | --- |\n| x\\|y | z |", color=False, width=40)
        self.assertIn("x|y", out)
        self.assertIn("z", out)

    def test_display_width_counts_cjk_as_two(self):
        self.assertEqual(_display_width("流域"), 4)
        self.assertEqual(_display_width("ab"), 2)
        self.assertEqual(_display_width("a流"), 3)

    def test_horizontal_rule_and_blockquote(self):
        out = render_markdown("> 注意写入\n\n---", color=False, width=40)
        self.assertIn(">", out)
        self.assertIn("注意写入", out)
        self.assertIn("-" * 10, out)

    def test_non_string_input_is_coerced(self):
        self.assertIn("123", render_markdown(123, color=False, width=40))

    def test_supports_color_is_false_without_tty(self):
        # Under the offline test runner stdout is not a TTY.
        self.assertFalse(supports_color())


if __name__ == "__main__":
    unittest.main()
