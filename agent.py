from agents import Agent, ModelSettings, RunConfig, RunContextWrapper
from agents.agent import StopAtTools
from state import TravelState

from tools import (
    search_train,
    calculate_mileage_value,
    retrieve_loyalty_benefits,
    compose_travel_decision,
)


BASE_INSTRUCTIONS = """
你是一名智能旅行规划助手。只处理当前用户请求，不把旧任务参数带入新任务。

可靠性规则：
1. 不得编造用户没有提供的参数、工具结果、票价、实时状态或知识来源。
2. 下方“结构化任务状态”是应用根据用户原文提取的可信状态；工具参数必须逐字使用其中的已确认值。
3. 缺失参数不为“无”时，不得调用对应工具，只询问真正缺失的字段。
4. 用户修改条件后，以结构化状态中的最新值为准，并简短确认修改。
   如果修改后当前查询/计算任务的参数仍完整且合法，应立即重新调用对应工具，
   不要再次询问“是否需要查询/计算”。
5. 用户切换任务后，只完成新任务；除非用户明确要求，不继续旧任务。
6. 中文提问使用中文回答，保持简洁清晰。

火车查询：
- 必须具备出发城市、目的城市、明确日期，才能调用 search_train。
- 模糊日期（如“下周”“周末”）必须追问明确日期。
- realtime=false 只能称为计划时刻，不能声称实时运行状态。
- 数据源不提供票价，不得估算票价。
- provider_error 表示服务暂不可用；no_results 表示当前数据源没有结果。

里程兑换：
- 必须具备 miles_required、cash_price、taxes，才能调用 calculate_mileage_value。
- 税费不能自行假设为 0；只有用户明确说明零税费才有效。
- 如果数值非法或税费高于现金票价，应要求用户确认。

常旅客权益：
- 必须调用 retrieve_loyalty_benefits，不使用模型记忆直接回答。
- out_of_scope 或 no_evidence 时明确拒答，不补充模型自身知识。
- success 时只能使用 evidence 支持结论，并列出 evidence 中实际返回的 source 和 source_url。
- 证据不足以支持明确结论时，应说明无法确认。

综合决策（current_task=decision）：
- 这是单 Agent 的依赖工作流。先分别调用 search_train、calculate_mileage_value；如果
  loyalty_requested=true，再调用 retrieve_loyalty_benefits。
- 每次模型决策都必须遵守结构化状态中的“必须执行的下一步”。该字段是应用生成的
  workflow control，不是建议；要求调用工具时，禁止只用对话历史中的旧结果直接回答。
- 不得在依赖工具尚未执行时自行给出推荐。依赖完成后必须调用 compose_travel_decision。
- 某个第三方工具返回 provider_error/no_results/no_evidence 时仍要继续其余工具，最后调用
  compose_travel_decision 生成降级结果；不得用模型知识填补失败数据。
- compose_travel_decision 的 JSON 是最终结果，不得改写其中事实、数值、证据或推荐。
"""


def travel_instructions(
    ctx: RunContextWrapper[TravelState],
    agent: Agent[TravelState],
) -> str:
    del agent
    return f"{BASE_INSTRUCTIONS}\n\n结构化任务状态：\n{ctx.context.prompt_context()}"


def run_config_for(state: TravelState) -> RunConfig | None:
    """Force every actionable domain request through its required tool."""

    if (
        state.current_task == "decision"
        and state.is_actionable("decision")
        and "decision" not in state.tool_results
    ):
        return RunConfig(model_settings=ModelSettings(tool_choice="required"))
    if (
        state.current_task == "train"
        and state.is_actionable("train")
        and "train" not in state.tool_results
    ):
        return RunConfig(model_settings=ModelSettings(tool_choice="required"))
    if (
        state.current_task == "mileage"
        and state.is_actionable("mileage")
        and "mileage" not in state.tool_results
    ):
        return RunConfig(model_settings=ModelSettings(tool_choice="required"))
    if state.current_task == "loyalty" and "loyalty" not in state.tool_results:
        return RunConfig(model_settings=ModelSettings(tool_choice="required"))
    return None


travel_agent = Agent[TravelState](
    name="Travel Planner",

    model="gpt-5.6-luna",

    model_settings={
        "reasoning": {"effort": "none"},
        "verbosity": "low",
        # Tool results are committed into shared structured state. Sequential
        # execution prevents parallel branches from racing on that state.
        "parallel_tool_calls": False,
    },

    instructions=travel_instructions,

    tools=[
        search_train,
        calculate_mileage_value,
        retrieve_loyalty_benefits,
        compose_travel_decision,
    ],
    tool_use_behavior=StopAtTools(stop_at_tool_names=["compose_travel_decision"]),
    # A decision run uses tool_choice=required until the deterministic composer
    # stops the run. Dynamic tool gating leaves only valid pending dependencies.
    reset_tool_choice=False,
)

# Single-domain runs require exactly one business tool, then a normal language
# response. Decision runs retain required tool choice across their dependency chain.
single_task_agent = travel_agent.clone(
    name="Travel Planner Single Task",
    reset_tool_choice=True,
)


def agent_for_state(state: TravelState) -> Agent[TravelState]:
    return travel_agent if state.current_task == "decision" else single_task_agent
