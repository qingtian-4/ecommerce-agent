# M8.1 / v3 评分合同

复用 `../v3_multiturn_m8_v2` 的原任务、初态和工具决定；manifest 校验其文件与实现指纹。
所有题目均为已见开发材料。没有新增未见测试集，没有改写 M8 v1/v2 成绩。

`sdk_open_answers.json` 只是新的固定回答边界反例，不提供给模型，也不是标准答案。
它保留原工具决定，用自然表达触发待审队列。不得把这些文本的程序待审结果当成人工评价。

入口为 `scripts/eval_live_readiness.py`，旧入口和旧评分器保留以复现历史结果。
新合同用 `raw_outcome` / `display_outcome` 区分 pass、fail、pending_review、execution_error。
兼容字段 `task_success` 对待审/执行异常为 null，不再用 false 代替未知。

原 task.success/failure 描述中的 unresolved-fails 仅解释历史 v2；本版本由新合同覆盖其
语义评分方式，SQL expect、工具权限、用户输入和 Fixture 均未修改。
