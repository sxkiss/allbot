#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信MQ消息解析工具
用于解析和提取微信消息的关键信息
"""

import json
from typing import Dict, Any, Optional
from datetime import datetime


class MQMessageParser:
    """微信MQ消息解析器"""
    
    @staticmethod
    def parse_message(raw_data: str) -> Dict[str, Any]:
        """
        解析原始MQ消息数据
        
        Args:
            raw_data: 原始JSON字符串
            
        Returns:
            解析后的消息字典
        """
        try:
            data = json.loads(raw_data)
            return MQMessageParser._extract_message_info(data)
        except json.JSONDecodeError as e:
            return {"error": f"JSON解析失败: {str(e)}"}
    
    @staticmethod
    def _extract_message_info(data: Dict) -> Dict[str, Any]:
        """提取消息核心信息"""
        if not data.get("Success") or data.get("Code") != 0:
            return {"error": "消息状态异常", "raw": data}
        
        msg_data = data.get("Data", {})
        add_msgs = msg_data.get("AddMsgs", [])
        
        if not add_msgs:
            return {"error": "无消息内容"}
        
        # 解析第一条消息
        msg = add_msgs[0]
        
        result = {
            "msg_id": msg.get("MsgId"),
            "new_msg_id": msg.get("NewMsgId"),
            "msg_type": MQMessageParser._get_msg_type(msg.get("MsgType")),
            "from_user": MQMessageParser._extract_username(msg.get("FromUserName")),
            "to_user": MQMessageParser._extract_username(msg.get("ToUserName")),
            "content": MQMessageParser._extract_content(msg.get("Content")),
            "push_content": MQMessageParser._extract_username(msg.get("PushContent")),
            "create_time": MQMessageParser._format_timestamp(msg.get("CreateTime")),
            "status": msg.get("Status"),
            "msg_seq": msg.get("MsgSeq"),
            "is_group": "@chatroom" in str(msg.get("FromUserName", {})),
        }
        
        # 解析群消息的发送者
        if result["is_group"]:
            content_str = str(msg.get("Content", {}).get("string", ""))
            if ":" in content_str:
                sender, actual_content = content_str.split(":", 1)
                result["group_sender"] = sender.strip()
                result["actual_content"] = actual_content.strip()
        
        # 解析MsgSource中的群信息
        msg_source = msg.get("MsgSource", "")
        if msg_source:
            result["msg_source_info"] = MQMessageParser._parse_msg_source(msg_source)
        
        return result
    
    @staticmethod
    def _get_msg_type(msg_type: int) -> str:
        """消息类型映射"""
        type_map = {
            1: "文本消息",
            3: "图片消息",
            34: "语音消息",
            43: "视频消息",
            47: "表情消息",
            49: "链接/小程序消息",
            10000: "系统消息",
        }
        return type_map.get(msg_type, f"未知类型({msg_type})")
    
    @staticmethod
    def _extract_username(user_obj: Any) -> Optional[str]:
        """提取用户名"""
        if isinstance(user_obj, dict):
            return user_obj.get("string")
        return str(user_obj) if user_obj else None
    
    @staticmethod
    def _extract_content(content_obj: Any) -> Optional[str]:
        """提取消息内容"""
        if isinstance(content_obj, dict):
            return content_obj.get("string")
        return str(content_obj) if content_obj else None
    
    @staticmethod
    def _format_timestamp(timestamp: int) -> str:
        """格式化时间戳"""
        if not timestamp:
            return ""
        try:
            dt = datetime.fromtimestamp(timestamp)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            return str(timestamp)
    
    @staticmethod
    def _parse_msg_source(msg_source: str) -> Dict[str, Any]:
        """解析MsgSource XML"""
        import re
        info = {}
        
        # 提取群成员数
        member_match = re.search(r'<membercount>(\d+)</membercount>', msg_source)
        if member_match:
            info["member_count"] = int(member_match.group(1))
        
        # 提取静音状态
        silence_match = re.search(r'<silence>(\d+)</silence>', msg_source)
        if silence_match:
            info["is_silence"] = silence_match.group(1) == "1"
        
        return info
    
    @staticmethod
    def format_output(parsed_data: Dict[str, Any]) -> str:
        """格式化输出解析结果"""
        if "error" in parsed_data:
            return f"❌ 错误: {parsed_data['error']}"
        
        output = ["📨 微信消息解析结果", "=" * 50]
        
        # 基本信息
        output.append(f"消息ID: {parsed_data.get('msg_id')}")
        output.append(f"消息类型: {parsed_data.get('msg_type')}")
        output.append(f"发送时间: {parsed_data.get('create_time')}")
        
        # 发送者和接收者
        if parsed_data.get("is_group"):
            output.append(f"群聊ID: {parsed_data.get('from_user')}")
            if parsed_data.get("group_sender"):
                output.append(f"发送者: {parsed_data.get('group_sender')}")
        else:
            output.append(f"发送者: {parsed_data.get('from_user')}")
        
        output.append(f"接收者: {parsed_data.get('to_user')}")
        
        # 消息内容
        content = parsed_data.get('actual_content') or parsed_data.get('content')
        output.append(f"消息内容: {content}")
        
        # 群信息
        if parsed_data.get("msg_source_info"):
            source_info = parsed_data["msg_source_info"]
            if "member_count" in source_info:
                output.append(f"群成员数: {source_info['member_count']}")
            if "is_silence" in source_info:
                status = "已静音" if source_info["is_silence"] else "未静音"
                output.append(f"静音状态: {status}")
        
        return "\n".join(output)


# 使用示例
if __name__ == "__main__":
    # 示例数据
    sample_data = '''{"Code":0,"Success":true,"Message":"成功","Data":{"AddMsgs":[{"MsgId":1557198438,"FromUserName":{"string":"52806025813@chatroom"},"ToUserName":{"string":"wxid_xdoez8q10f6229"},"MsgType":1,"Content":{"string":"sxkiss_com:\\nw"},"Status":3,"CreateTime":1764153850,"MsgSource":"<msgsource>\\n\\t<membercount>5</membercount>\\n</msgsource>","PushContent":"与你相约 : w","NewMsgId":4223379567962673026,"MsgSeq":337752}]}}'''
    
    parser = MQMessageParser()
    result = parser.parse_message(sample_data)
    print(parser.format_output(result))