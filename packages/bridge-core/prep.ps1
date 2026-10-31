$ErrorActionPreference='Continue'
$py='C:\Users\one\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
& $py -m pip --version 2>&1 | Out-String
& $py -m pip install --disable-pip-version-check --target 'E:\useer\deepseek-harness\default-workspace\packages\bridge-core\vendor' -i https://pypi.tuna.tsinghua.edu.cn/simple edge-tts sherpa-onnx soundfile numpy pilk 2>&1 | Select-Object -Last 8 | Out-String
$base='https://hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main'
curl.exe -sSL --retry 2 -o 'E:\useer\deepseek-harness\default-workspace\packages\bridge-core\models\model.int8.onnx' "$base/model.int8.onnx"
curl.exe -sSL --retry 2 -o 'E:\useer\deepseek-harness\default-workspace\packages\bridge-core\models\tokens.txt' "$base/tokens.txt"
"MODEL $((Get-Item 'E:\useer\deepseek-harness\default-workspace\packages\bridge-core\models\model.int8.onnx').Length) TOKENS $((Get-Item 'E:\useer\deepseek-harness\default-workspace\packages\bridge-core\models\tokens.txt').Length)"
'DONE-PREP'