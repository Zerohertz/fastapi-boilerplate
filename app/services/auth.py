from datetime import datetime, timedelta

import httpx
import pytz
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError
from passlib.context import CryptContext

from app.core.configs import configs
from app.exceptions.auth import (
    GitHubOAuthFailed,
    OAuthFormDataInvalid,
    TokenDecodeError,
    TokenExpired,
)
from app.models.users import User
from app.schemas.auth import GitHubOAuthRequest, GitHubOAuthResponse, JwtPayload


class CryptService(CryptContext):
    def __init__(self) -> None:
        super().__init__(schemes=["bcrypt"], deprecated="auto")

    def hash(self, secret: str) -> str:  # type: ignore[override]
        return super().hash(secret=secret)

    def verify(self, secret: str, hash: str) -> bool:  # type: ignore[override]
        return super().verify(secret=secret, hash=hash)


class JwtService:
    def __init__(self) -> None:
        self.secret = configs.JWT_SECRET_KEY
        self.algorithm = configs.JWT_ALGORITHM
        self.access_expire = timedelta(hours=2)
        self.refresh_expire = timedelta(days=1)

    def _encode(self, *, sub: str, exp: timedelta) -> str:
        payload = JwtPayload(
            sub=sub,
            iat=datetime.now().astimezone(pytz.timezone(configs.TZ)),
            exp=datetime.now().astimezone(pytz.timezone(configs.TZ)) + exp,
        )
        return jwt.encode(
            claims=payload.model_dump(), key=self.secret, algorithm=self.algorithm
        )

    def create_access_token(self, user: User) -> str:
        return self._encode(sub=str(user.id), exp=self.access_expire)

    def create_refresh_token(self, user: User) -> str:
        return self._encode(sub=f"{user.id}.refresh", exp=self.refresh_expire)

    def decode(self, *, token: str) -> str:
        try:
            payload = jwt.decode(
                token=token, key=self.secret, algorithms=self.algorithm
            )
            return payload["sub"]
        except ExpiredSignatureError as error:
            raise TokenExpired from error
        except JWTError as error:
            raise TokenDecodeError from error


class GitHubService:
    def __init__(self) -> None:
        self.client_id = configs.GITHUB_OAUTH_CLIENT_ID
        self.client_secret = configs.GITHUB_OAUTH_CLIENT_SECRET

    async def get_token_and_user(
        self, schema: GitHubOAuthRequest
    ) -> GitHubOAuthResponse:
        if schema.grant_type != "authorization_code":
            raise OAuthFormDataInvalid
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    "https://github.com/login/oauth/access_token",
                    json={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "code": schema.code,
                        "redirect_uri": schema.redirect_uri,
                    },
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise GitHubOAuthFailed from error
        data = response.json()
        github_token = data.get("access_token", None)
        if github_token is None:
            raise GitHubOAuthFailed
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    "https://api.github.com/user",
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {github_token}",
                    },
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise GitHubOAuthFailed from error
        github_user = response.json()
        github_name, github_email = github_user.get("login", None), github_user.get(
            "email", None
        )
        if github_name is None or github_email is None:
            raise GitHubOAuthFailed
        return GitHubOAuthResponse(
            token=github_token, name=github_name, email=github_email
        )
