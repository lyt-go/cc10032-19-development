"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import lineage_impact, resource

# 固定前缀路由先注册，资源路由的 /api/{module} 动态路径放最后，避免抢先匹配。
ROUTERS = [lineage_impact, resource]
