import os
from app.services.knowledge_service import QAModule

# 模拟调用 /api/knowledge-base/list 端点的逻辑
def check_knowledge_bases():
    print("检查知识库列表...")
    
    # 扫描文件系统中的知识库文件
    knowledge_bases = []
    for file in os.listdir('.'):
        if file.endswith('_vector_store.pkl'):
            # 提取知识库名称
            kb_name = file.replace('knowledge_base_', '').replace('_vector_store.pkl', '')
            if kb_name not in knowledge_bases:
                knowledge_bases.append(kb_name)
                # 初始化知识库
                QAModule(kb_name)
    
    # 确保默认知识库存在
    if 'default' not in knowledge_bases:
        knowledge_bases.append('default')
        # 初始化默认知识库
        QAModule('default')
    
    print(f"当前知识库列表: {knowledge_bases}")
    
    # 验证 test_persistence 知识库是否存在
    test_kb_name = "test_persistence"
    if test_kb_name in knowledge_bases:
        print(f"✅ 验证通过: 知识库 {test_kb_name} 在重启后仍然存在")
    else:
        print(f"❌ 验证失败: 知识库 {test_kb_name} 在重启后消失了")

if __name__ == "__main__":
    check_knowledge_bases()
