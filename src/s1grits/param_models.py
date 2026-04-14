from pydantic import BaseModel, ValidationInfo, field_validator


class LookbackStrategyParams(BaseModel):
    """Pydantic model for validating lookback strategy parameters."""

    lookback_strategy: str
    max_pre_imgs_per_burst: int | list[int] | tuple[int, ...]
    delta_lookback_days: int | list[int] | tuple[int, ...]
    min_pre_imgs_per_burst: int
    delta_window_days: int

    @field_validator('delta_window_days')
    @classmethod
    def validate_delta_window_days(cls, v: int) -> int:
        """Validate that delta_window_days is less than 365 days."""
        if v > 365:
            raise ValueError('delta_window_days must be less than 365 days.')
        return v

    @field_validator('lookback_strategy')
    @classmethod
    def validate_lookback_strategy(cls, v: str) -> str:
        """Validate that lookback_strategy is one of the supported values."""
        allowed_strategies = ['immediate_lookback', 'multi_window']
        if v not in allowed_strategies:
            raise ValueError(f'lookback_strategy must be one of {allowed_strategies}, got {v}')
        return v

    @field_validator('max_pre_imgs_per_burst')
    @classmethod
    def validate_max_pre_imgs_per_burst(
        cls, v: int | list[int] | tuple[int, ...], info: ValidationInfo
    ) -> int | tuple[int, ...]:
        """Validate max_pre_imgs_per_burst based on lookback_strategy."""
        lookback_strategy = info.data.get('lookback_strategy')

        if lookback_strategy == 'immediate_lookback':
            if isinstance(v, list | tuple):
                raise ValueError('max_pre_imgs_per_burst must be a single integer for immediate lookback strategy.')

        elif lookback_strategy == 'multi_window':
            if isinstance(v, int):
                v = (v,) * 3
            elif isinstance(v, list):
                v = tuple(v)

        return v

    @field_validator('delta_lookback_days')
    @classmethod
    def validate_delta_lookback_days(
        cls, v: int | list[int] | tuple[int, ...], info: ValidationInfo
    ) -> int | tuple[int, ...]:
        """Validate delta_lookback_days based on lookback_strategy and max_pre_imgs_per_burst."""
        lookback_strategy = info.data.get('lookback_strategy')
        max_pre_imgs_per_burst = info.data.get('max_pre_imgs_per_burst')

        if lookback_strategy == 'immediate_lookback':
            if v != 0:
                raise ValueError('delta_lookback_days must be 0 for immediate lookback strategy.')

        elif lookback_strategy == 'multi_window':
            if isinstance(v, int):
                if isinstance(max_pre_imgs_per_burst, list | tuple):
                    v = tuple(v * i for i in range(1, len(max_pre_imgs_per_burst) + 1))
                else:
                    v = tuple(v * i for i in range(1, 3 + 1))  # Default to 3 if max_pre_imgs_per_burst is still an int
            elif isinstance(v, list):
                v = tuple(v)

            if isinstance(max_pre_imgs_per_burst, list | tuple) and len(v) != len(max_pre_imgs_per_burst):
                raise ValueError(
                    'max_pre_imgs_per_burst and delta_lookback_days must have the same length. '
                    'If max_pre_imgs_per_burst is a single integer, this is interpreted as the maximum '
                    'number of pre-images on 3 anniversary dates so ensure that `delta_lookback_days` '
                    'is a tuple of length 3 or an integer.'
                )

        return v

    @field_validator('min_pre_imgs_per_burst')
    @classmethod
    def validate_min_pre_imgs_per_burst(cls, v: int, info: ValidationInfo) -> int:
        """Validate that all max_pre_imgs_per_burst values are greater than min_pre_imgs_per_burst."""
        max_pre_imgs_per_burst = info.data.get('max_pre_imgs_per_burst')
        lookback_strategy = info.data.get('lookback_strategy')

        if lookback_strategy == 'immediate_lookback':
            if isinstance(max_pre_imgs_per_burst, int) and max_pre_imgs_per_burst < v:
                raise ValueError('max_pre_imgs_per_burst must be greater than min_pre_imgs_per_burst')

        elif lookback_strategy == 'multi_window':
            if isinstance(max_pre_imgs_per_burst, list | tuple):
                if any(m < v for m in max_pre_imgs_per_burst):
                    raise ValueError('All values in max_pre_imgs_per_burst must be greater than min_pre_imgs_per_burst')
            if isinstance(max_pre_imgs_per_burst, int) and max_pre_imgs_per_burst < v:
                raise ValueError('max_pre_imgs_per_burst must be greater than min_pre_imgs_per_burst')

        return v
