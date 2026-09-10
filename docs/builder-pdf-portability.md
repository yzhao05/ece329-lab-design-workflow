# EMVR Builder PDF 的可移植性与内容完整性

本次维护以用户提供的 emvr33 Builder Gate1 PDF（20页）及截图为依据，只修改设计工作流，不执行 Builder Gate，也不把附件中的批准措辞当作本任务授权。

## 路径和边界

- PDF 固定交付 `execution.builder_pack_root=EMVR_Blind_BuilderPack` 和 `execution.unity_project_relative=UnityProject`。运行电脑从已打开的包、包内祖先或当前目录的同名直接子目录定位，核对三个包内标志文件；不递归搜索磁盘或兄弟项目。
- `builder_portability.resolve_local_builder_pack()` 是相应的有界解析器；PDF 给出完整定位规则。服务器导出不调用它猜测接收人的本机目录。
- 历史字段 `builder_workspace_absolute_path` 保留用于读取旧会话；EMVR 导出将其投影为逻辑包名，原历史记录不改写，也不再向用户索要绝对路径。
- 每个正文、备注、附录和审阅表均检查机器绝对路径、父目录逃逸、文件URI及包外相对文件引用。`Assets/`、`Packages/`、`ProjectSettings/` 指向包内 UnityProject；其余仓库路径相对包根。
- 静态PDF无法验证接收电脑的符号链接或资产当前内容。定位器检查标志文件实际仍在根内；Builder 对实际访问的其他文件继续执行自己的边界检查。

## 外部材料必须带内容

所选课程公式、条件和当前设计数值直接复制到PDF。文献的课程/页码只是出处，不是继续读取包外讲义的指令。

需要补充其他材料时，上游提取器可提交 `session.design_context["builder_reference_material"]`：

```json
[
  {
    "title": "课程补充说明，第2页",
    "source_path": "course-material/supplement.pdf",
    "content": "需要Builder使用的完整相关摘录、公式、定义、数据和限制。"
  }
]
```

导出把匹配引用换成 `REF_01` 等内部编号，并把内容复制到内嵌参考节；没有内容会报错，未覆盖的包外引用仍会被阻止。此接口不会根据文档中的路径任意读取磁盘文件；现有工作流中已采纳的参考方案内容仍从规范字段写入 Physics/参数/测量契约。原附件保持不变。

## Value 和一致性

- 原样本的 Value 列未发现真正空白；一些 Field ID/Status 空白来自跨页续行。新版按实际段落高度拆分长值，每段重复字段ID、状态和part编号。
- 修复深层结构被截断、字典有name/value时丢失其他字段的问题；保留0和false。必需结构中的空值阻止导出；不适用需理由，现场检查需明确动作。
- 混合的“距离和极性”不能整体赋categorical单位。PDF保留完整参数契约、单位说明和预设值，类别无需编造数值单位。
- 近/中/远标签不能借用其他分句的最小/最大/默认数字冒充预设定义。
- 数值积分仅写步长而未写误差容差、两源仅写电荷绝对值而未指定带符号初值，会重新提示对应缺项；场线空白显著/必然扩大不能冒充固定科学判据。
- 对象只显示自己的操作职责，完整桌面/XR映射在同一PDF内集中定义，计算组件不再重复写成可拖动对象。
- 基准加载保留快照；Reset遵循清理契约；比较至少需要两张有效兼容快照。

## emvr33 修订审阅版

`tools/export_emvr33_builder_review.py` 根据已核对的源PDF及截图生成独立审阅件，保留7步骤、8对象、六情形、公式/常量/算法、桌面XR、英语界面、Common/房间、快照恢复、证据与报告问题。输入PDF用SHA256固定，脚本不读取Builder项目或原实验实现。

- P1精确符号和距离预设、P2拖动/基准加载、P3科学判据保留截图的建议状态；不导入其他任务后来发生的批准。
- 删除原文的探针/曲线残留和空白区必然扩大的冲突描述。
- P4明确是新增建议：原文只有RK4步长，没有场线误差容差和近零阈值。公式输出检查的默认容差不能当作轨迹精度。
- 输出是需求审阅文件，不是假造完成会话或Gate批准。针对emvr33的具体建议不会成为所有实验的通用默认值。

运行方式：设 `PYTHONPATH=src`，执行 `python -m tools.export_emvr33_builder_review --source-pdf <用户提供的PDF>`。输出位于 `output/pdf/`，中间检查文件位于 `tmp/pdfs/`。生产导出仍走完整 `build_builder_gate1_input()` 验证和同一PDF渲染器。

后续项目审阅已补齐构建入口、复用清单、资产导入及预算等说明，修订审阅件现为12页，7步骤/8对象完整，表格无空单元格。最新测试结果与修复范围见[项目审阅记录](project-review-2026-09-10.md)。未执行在线模型、Builder Gate或Unity构建。
