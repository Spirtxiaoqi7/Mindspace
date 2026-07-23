# 语音通话与面对面互动

## 产品行为

点击“实时语音”后，前端显示两种互动方式：

- `通话`：默认选项，完全保留原有实时语音、ASR、打断、TTS 和连续陪伴逻辑。
- `面对面`：允许用户填写当前场景。角色仍通过语音回应，但可以自然描述自身
  外观、动作、朝向、距离变化和互动产生的体感线索。

用户默认不能直接看到角色画面。面对面模式的目标，是让角色通过可朗读的语言
建立现场感，而不是在每句话中机械插入动作旁白。角色只能确定自身动作和意图；
用户没有明确表达时，不能替用户决定动作、反应、情绪或触觉感受。

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

## 验证位置

- `tests/test_api.py`：默认值、校验、迁移和重启后恢复。
- `tests/test_graph.py`：通话与面对面 Prompt 隔离。
- `tests/test_prompt_cache_layout.py`：高优先级层位置和非持久化属性。
- `frontend/src/App.test.tsx`：弹窗、上次选择恢复、场景保存和语音页标识。
