window.__ModuleLoader__.load({
	id: "@xfy/console-web",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
		const React = require("react");
		const h = React.createElement;

		const ROUTE = "/api/dsh/xfy";
		const inject = ["slots"];

		async function api(action, body) {
			const payload = { ...(body || {}), action };
			const res = await fetch(ROUTE, {
				method: "POST",
				headers: { "content-type": "application/json" },
				body: JSON.stringify(payload)
			});
			return await res.json();
		}

		const card = { border: "1px solid var(--dsh-border, rgba(127,127,127,.25))", borderRadius: 10, padding: "12px 14px", marginBottom: 12 };
		const row = { display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" };
		const label = { fontSize: 12, opacity: 0.7, minWidth: 64 };
		const btn = (primary) => ({
			padding: "6px 14px",
			borderRadius: 8,
			cursor: "pointer",
			fontSize: 13,
			border: "1px solid " + (primary ? "transparent" : "rgba(127,127,127,.35)"),
			background: primary ? "#2f6bff" : "transparent",
			color: primary ? "#fff" : "inherit"
		});
		const input = { padding: "6px 10px", borderRadius: 8, border: "1px solid rgba(127,127,127,.35)", background: "transparent", color: "inherit", fontSize: 13 };

		function dot(alive) {
			return h("span", {
				style: {
					width: 10, height: 10, borderRadius: "50%", display: "inline-block",
					background: alive ? "#22c55e" : "#ef4444",
					boxShadow: alive ? "0 0 8px rgba(34,197,94,.8)" : "none"
				}
			});
		}

		function ConsolePanel() {
			const [status, setStatus] = React.useState(null);
			const [cfg, setCfg] = React.useState(null);
			const [busy, setBusy] = React.useState("");
			const [notice, setNotice] = React.useState("");
			const [loop, setLoop] = React.useState(null);
			const [persona, setPersona] = React.useState("");
			const [strategy, setStrategy] = React.useState({});

			const refresh = React.useCallback(async () => {
				try {
					const s = await api("status");
					setStatus(s);
					if (s && s.core && s.core.ok) {
						const c = await api("config");
						if (c && c.ok) {
							setCfg(c.config);
							setPersona(((c.config.persona || {}).profile) || "");
							setStrategy({ ...(c.config.strategy || {}) });
						}
					}
				} catch (error) {
					setStatus({ ok: false, error: String(error && error.message) });
				}
			}, []);

			React.useEffect(() => {
				refresh();
				const timer = setInterval(refresh, 5000);
				return () => clearInterval(timer);
			}, [refresh]);

			const run = async (action, body, tag) => {
				setBusy(tag);
				setNotice("");
				try {
					const res = await api(action, body);
					setNotice(JSON.stringify(res).slice(0, 400));
					if (action === "loopback") setLoop(res);
					await refresh();
				} catch (error) {
					setNotice(String(error && error.message));
				} finally {
					setBusy("");
				}
			};

			const alive = !!(status && status.bridge && status.bridge.alive);
			const coreOk = !!(status && status.core && status.core.ok);
			const asrReady = !!(status && status.core && status.core.asr && status.core.asr.ready);

			const header = h("div", { style: { ...row, justifyContent: "space-between", marginBottom: 10 } }, [
				h("div", { style: row, key: "s" }, [dot(alive), h("strong", { key: "t" }, "小肥鱼桥"), h("span", { key: "p", style: { opacity: 0.7, fontSize: 12 } }, alive ? "运行中 pid " + status.bridge.pid : "已停止")]),
				h("button", { key: "r", style: btn(false), onClick: refresh, disabled: !!busy }, "刷新")
			]);

			const badges = h("div", { style: { ...row, marginBottom: 12 } }, [
				h("span", { key: "a", style: { ...label, minWidth: 0 } }, "ASR " + (asrReady ? "就绪" : "未就绪")),
				h("span", { key: "b", style: { ...label, minWidth: 0 } }, "TTS " + ((status && status.core && status.core.tts && status.core.tts.provider) || "-")),
				h("span", { key: "c", style: { ...label, minWidth: 0 } }, coreOk ? "核心在线" : "核心离线")
			]);

			const controls = h("div", { style: { ...card } }, [
				h("div", { style: { ...row, marginBottom: 10 } }, [
					h("button", { key: "start", style: btn(true), disabled: !!busy || alive, onClick: () => run("bridge.start", undefined, "start") }, busy === "start" ? "启动中…" : "启动"),
					h("button", { key: "stop", style: btn(false), disabled: !!busy || !alive, onClick: () => run("bridge.stop", undefined, "stop") }, busy === "stop" ? "停止中…" : "停止"),
					h("button", { key: "restart", style: btn(false), disabled: !!busy, onClick: () => run("bridge.restart", undefined, "restart") }, "重启")
				]),
				h("div", { style: { ...row, marginBottom: 10 } }, [
					h("button", { key: "loop", style: btn(false), disabled: !!busy || !coreOk, onClick: () => run("loopback", { text: "小肥鱼语音链路自检" }, "loop") }, "语音自检（TTS→ASR）"),
					h("button", { key: "tts", style: btn(false), disabled: !!busy || !coreOk, onClick: () => run("tts", { text: "你好，我是小肥鱼。" }, "tts") }, "发一句测试音")
				]),
				loop ? h("div", { key: "lr", style: { fontSize: 12, lineHeight: 1.7 } }, [
					h("div", { key: "1" }, "输入：" + loop.input),
					h("div", { key: "2" }, "识别：" + loop.asr_text),
					h("div", { key: "3", style: { opacity: 0.7 } }, "音频：" + loop.tts_path + "（" + loop.duration_ms + "ms）")
				]) : null
			]);

			const personaBox = h("div", { style: card }, [
				h("div", { key: "h", style: { fontSize: 13, fontWeight: 600, marginBottom: 8 } }, "人设（persona.profile）"),
				h("textarea", {
					key: "t",
					value: persona,
					onChange: (e) => setPersona(e.target.value),
					rows: 5,
					style: { ...input, width: "100%", resize: "vertical", lineHeight: 1.6, boxSizing: "border-box" }
				})
			]);

			const keys = ["max_reply_chars", "reply_chars_min", "reply_chars_max", "tease_every"];
			const strategyBox = h("div", { style: card }, [
				h("div", { key: "h", style: { fontSize: 13, fontWeight: 600, marginBottom: 8 } }, "回复策略"),
				h("div", { style: row }, keys.map((k) => h("label", { key: k, style: { ...row, gap: 6 } }, [
					h("span", { key: "l", style: { ...label, minWidth: 0 } }, k),
					h("input", {
						key: "i",
						type: "number",
						value: strategy[k] === undefined ? "" : strategy[k],
						onChange: (e) => setStrategy({ ...strategy, [k]: Number(e.target.value) }),
						style: { ...input, width: 92 }
					})
				]))),
				h("div", { key: "save", style: { ...row, marginTop: 12 } }, [
					h("button", {
						key: "s",
						style: btn(true),
						disabled: !!busy || !coreOk,
						onClick: () => run("config.save", { persona: { profile: persona }, strategy }, "save")
					}, busy === "save" ? "保存中…" : "保存到桥配置"),
					h("span", { key: "n", style: { fontSize: 12, opacity: 0.65 } }, "写回 wechat-bridge/config.json，桥自动热加载")
				])
			]);

			const logs = status && status.log && status.log.length
				? h("div", { style: card }, [
					h("div", { key: "h", style: { fontSize: 13, fontWeight: 600, marginBottom: 8 } }, "桥日志（尾部）"),
					h("pre", {
						key: "p",
						style: { fontSize: 11, lineHeight: 1.55, margin: 0, whiteSpace: "pre-wrap", opacity: 0.8, maxHeight: 180, overflow: "auto" }
					}, status.log.join("\n"))
				])
				: null;

			const noticeBox = notice
				? h("div", { style: { ...card, fontSize: 12, lineHeight: 1.6, opacity: 0.85 } }, notice)
				: null;

			return h("div", null, [header, badges, controls, personaBox, strategyBox, logs, noticeBox]);
		}

		function apply(ctx) {
			ctx.slots.inject("settings.section", () =>
				ctx.slots.register({
					name: "settings.section",
					id: "xfy-console",
					order: 120,
					label: () => "小肥鱼控制台"
				}, ConsolePanel)
			);
		}

		exports.apply = apply;
		exports.inject = inject;
		return module.exports;
	}
});
