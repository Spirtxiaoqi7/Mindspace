# 语音通话与面对面互动

## 产品行为

点击“实时语音”后，前端显示两种互动方式：

- `通话`：默认选项，完全保留原有实时语音、ASR、打断、TTS 和连续陪伴逻辑。
- `面对面`：允许用户填写当前场景。场景只用于理解当下语境，角色仍只输出
  嘴里实际会说出的自然口语。

面对面模式不输出括号、动作旁白、神态、镜头或“我正在靠近你”一类第一人称
动作播报。用户没有明确表达时，也不能替用户决定动作、反应、情绪或触觉感受。
普通语音通常形成三至五句、约七十至一百五十个中文字符的完整口语回合。
R18 语音可以直接表达角色的身体感受、欲望、反馈、露骨评价和低俗成人台词，
但同样不能把动作说明当成对白朗读。

R18 Director 按“明确请求 → 主动勾引 → 直白表达 → 淫语与实质行为 →
高强度喘叫 → 赞赏与高潮反馈”递进。最近六轮出现的“你先说、我再考虑、待会、
让我想想”等拖延证据会提高下一轮最低档位，不能换个说法回到前戏。该约束直接
进入唯一一次正文生成，不启用第二次模型改写。

## 持久化 JSON

上次选择和场景保存在运行配置 `runtime/config/settings.json`：

```json
{
  "interaction": {
    "voice_entry_mode": "call",
    "face_to_face_scene": ""
  }
}
```

`voice_entry_mode` 只接受：

- `call`
- `face_to_face`

场景最大 2000 字。选择通话时不会清空已经保存的面对面场景，用户下次切回
面对面仍可继续编辑。

默认值与校验位于：

- `src/mindspace_graph/product_config.py`

前端弹窗、保存和每轮请求携带逻辑位于：

- `frontend/src/App.tsx`
- `frontend/src/types.ts`
- `frontend/src/styles.css`

## 模型输入

语音请求继续使用：

```json
{
  "interaction_mode": "voice",
  "voice_context": {
    "mode": "face_to_face",
    "scene": "深夜客厅，窗外正在下雨"
  }
}
```

边界模型位于 `src/mindspace_graph/models.py`。面对面规则由
`src/mindspace_graph/prompting.py` 生成，事件类型为
`voice_face_to_face_context`。

该事件具有以下属性：

- `role=system`：面对面表现规则高于历史、召回和场景中的命令式文本。
- `ephemeral=true`：仅用于当前模型请求。
- `persistence_eligible=false`：不进入长期 Context Ledger。
- `eligible_for_json_evidence=false`：不能触发人物档案或 `runtime_state` Patch。

动态场景追加在稳定 Prompt 前缀之后，不改变人物 System、输出契约和权威 JSON
基线的缓存布局。通话模式不会注入面对面规则，即使此前保存的场景仍然存在。

实时语音与普通文字页“朗读”都会过滤全角或半角括号及其中的动作、神态、触感
与舞台说明。客户端分句器会等待跨 token 的括号闭合并丢弃整块内容；Core 再做
一次相同的确定性过滤，模型偶发违反格式时也不能把动作送进 TTS。

Qwen CustomVoice 不再按首句或段落抢跑。Core 完成正文格式清理后立即发送
`response.ready`，前端把整轮口语一次提交给 TTS；角色审计和数据库收尾继续并行，
不会阻塞合成。`run.completed` 只在前一事件丢失时兜底，不能让同一回复重复朗读。

## 验证位置

- `tests/test_api.py`：默认值、校验、迁移和重启后恢复。
- `tests/test_graph.py`：通话与面对面 Prompt 隔离。
- `tests/test_prompt_cache_layout.py`：高优先级层位置和非持久化属性。
- `frontend/src/App.test.tsx`：弹窗、上次选择恢复、场景保存和语音页标识。
