"""测试 API Pool 各模型能力"""
import base64, glob, time
from openai import OpenAI

client = OpenAI(api_key='not-needed', base_url='http://localhost:8765/v1')

# 测试简单回复
for model in ['api-pool-chat', 'api-pool-fast', 'api-pool-long', 'api-pool', 'api-pool-coding', 'api-pool-vision']:
    try:
        t0 = time.time()
        resp = client.chat.completions.create(
            model=model,
            messages=[{'role': 'user', 'content': '回复一个字：好'}],
            max_tokens=10
        )
        elapsed = time.time() - t0
        print(f'✅ {model}: {resp.choices[0].message.content} ({elapsed:.1f}s)')
    except Exception as e:
        print(f'❌ {model}: {str(e)[:80]}')

print('\n--- 测试 vision 模型看图 ---')
images = glob.glob(r'E:\workspace_backup\lecture-generator\images\source\**\*.png', recursive=True)
if images:
    img_path = images[0]
    with open(img_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('utf-8')
    try:
        resp = client.chat.completions.create(
            model='api-pool-vision',
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': '这张图有什么数学内容？简短回答'},
                    {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}}
                ]
            }],
            max_tokens=200
        )
        print(f'✅ vision看图成功: {resp.choices[0].message.content[:200]}')
    except Exception as e:
        print(f'❌ vision看图失败: {str(e)[:200]}')
else:
    print('没有找到测试图片')
